# python
import csv
import datetime
import json
import StringIO
import time
from decimal import Decimal

# django
from django.contrib.auth.models import User
from django.db import models

# extranet

# this package
from ._needs import Project
from ._issues import Issue, Repository
from .utils import HoursReporter, Nameable


# === schema-related ===

HOURS_IDENTITY_FIELDS = (
    'coder', 'project', 'date', 'amount', 'start_time', 'issue', 'repository',
    'comment',
)
HOURS_EXTRA_FIELDS = (
    'end_time', 'input_data_json',
)


# === utils  ===

def parse_time(x, time_formats):
    if not x:
        return None

    for frmt in time_formats:
        try:
            t = time.strptime(x, frmt)
            return datetime.time(t.tm_hour, t.tm_min, t.tm_sec) if t else None
        except:
            pass


# === exceptions ===

class AtLeastOneTagRequired(Exception):
    pass


class NoCoderProjects(Exception):
    pass


# === managers ===

class MonthManager(models.Manager):

    def get_from_date(self, date):
        return Month.objects.get_or_create(year=date.year, month=date.month)[0]


class HoursManager(models.Manager):

    DATE_FORMAT = '%Y-%m-%d'
    TIME_FORMATS = ['%H:%M', '%I:%M:%S %p']

    def _csv_parse(self, row, coder=None):

        if not coder:
            usr, proj, date, strt, end, hrs, tags, repo, iss, comment = row
            coder = Coder.get(username=usr)
        else:
            proj, date, strt, end, hrs, tags, repo, iss, comment = row

        # === tags ===
        tags = filter(None, [x.strip().lower() for x in tags.split(',')])
        if not tags:
            raise AtLeastOneTagRequired()

        # === repository and issue ===
        repository = Repository.objects.try_to_get_by_name(repo)
        issue = repository.try_to_get_issue(iss) if repository else None

        # === project ===
        a, b = None, None
        if proj:
            a = Project.objects.get(name=proj)
        if issue:
            b = issue.try_to_get_project()
        if a and b:
            assert(a == b)
        project = a or b
        assert(project)

        obj = Hours(
            project=project,
            date=datetime.datetime.strptime(date, self.DATE_FORMAT).date(),
            amount=Decimal(hrs or '0'),
            coder=coder.user,
            repository=repository,
            issue=issue,
            start_time=parse_time(strt, self.TIME_FORMATS),
            end_time=parse_time(end, self.TIME_FORMATS),
            comment=comment,
            input_data_json=json.dumps(row)
        )
        assert project in coder.projects, u'Not allowed'

        # don't save yet!

        obj._tags_to_be = tags

        return obj

    def csv_dump(self, obj, omit_coder=False):

        # === prepare values ===
        frmt_time = lambda t: t.strftime(self.TIME_FORMATS[0]) if t else ''
        tags = [t.name for t in obj.tags.all()] if obj.pk else obj._tags_to_be
        values = [] if omit_coder else [obj.coder.username]
        values += [
            obj.project.name if obj.project else '',
            obj.date.strftime(self.DATE_FORMAT),
            frmt_time(obj.start_time),
            frmt_time(obj.end_time),
            obj.amount,
            u','.join(tags),
            obj.repository.get_distinct_name() if obj.repository else '',
            obj.issue.number if obj.issue else '',
            obj.comment or '',
        ]

        # === dump to csv ===
        tmp = StringIO.StringIO()
        writer = csv.writer(tmp, delimiter=';')

        def utf8_safe(value):
            if isinstance(value, str):
                value = value.decode('utf-8')
            return unicode(value).encode(u'utf-8')
        writer.writerow(map(utf8_safe, values))
        row = tmp.getvalue()
        tmp.close()

        return row

    def csv_get_or_create(self, row, coder=None):

        parsed = self._csv_parse(row, coder=coder)
        assert(parsed._tags_to_be)

        # get unique object
        q = dict((key, getattr(parsed, key)) for key in HOURS_IDENTITY_FIELDS)
        obj, created = Hours.objects.get_or_create(**q)

        # allow overwriting some fields
        for key in HOURS_EXTRA_FIELDS:
            setattr(obj, key, getattr(parsed, key))

        # overwrite tags, regardless of whether the obj was created of not
        tags = []
        for tag_name in parsed._tags_to_be:
            tag, ignore = HourTag.objects.get_or_create(name=tag_name)
            tags.append(tag)
        obj.tags = tags

        obj.save()

        return obj, created


# === models ===

class HourTag(Nameable, HoursReporter):
    def iter_hours(self):
        for hours in self.hours_set.all():
            yield hours


class Month(models.Model):
    year = models.IntegerField()
    month = models.IntegerField()

    objects = MonthManager()

    def total_coder_hours(self):
        return sum(hours.amount for hours in self.billable_coder_hours.all())

    def total_project_hours(self):
        return sum(hours.amount for hours in self.billable_project_hours.all())

    def __unicode__(self):
        return u'{}-{:02d}'.format(self.year, self.month)


class Hours(models.Model):

    objects = HoursManager()

    # required
    coder = models.ForeignKey(User)
    project = models.ForeignKey(Project)
    date = models.DateField()
    amount = models.DecimalField(max_digits=4, decimal_places=2)

    # special, at least one required
    tags = models.ManyToManyField(HourTag)

    # optional
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    issue = models.ForeignKey(Issue, null=True, blank=True)
    repository = models.ForeignKey(Repository, null=True, blank=True)

    comment = models.TextField(null=True, blank=True)

    # meta
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)
    input_data_json = models.TextField(null=True, blank=True)  # sys info

    # billing
    coder_billing_month = models.ForeignKey(
        Month, null=True, blank=True, related_name='billable_coder_hours'
    )
    project_billing_month = models.ForeignKey(
        Month, null=True, blank=True, related_name='billable_project_hours'
    )

    class Meta:
        verbose_name_plural = 'Hours'

    def __unicode__(self):
        return u'{} | {} | {} | {} | {} '.format(
            self.coder.email,
            self.project,
            self.issue.repository.get_distinct_name() if self.issue else None,
            self.issue.number if self.issue else None,
            self.date,
            self.amount,
        )

    def get_need(self):
        if self.issue:
            return self.issue.need

    def tags_string(self):
        return u', '.join(x.name for x in self.tags.all())

    def ticket_info(self):
        if self.issue:
            return self.issue.need or self.issue.title
        else:
            return self.comment

    def as_scsv(self):
        ''' semicolon-separated values '''
        return Hours.objects.csv_dump(self)

    def as_scsv_without_coder(self):
        ''' semicolon-separated values '''
        return Hours.objects.csv_dump(self, omit_coder=True)

    def similar_row_exists(self):
        ''' can be used eg. when parsing new hours from CSV files '''

        try:
            q = dict((key, getattr(self, key)) for key in
                     HOURS_IDENTITY_FIELDS)
            obj = Hours.objects.get(**q)
        except Hours.DoesNotExist:
            obj = None
        return obj is not None

    def set_default_coder_billing_month(self):
        if not self.coder_billing_month:
            self.coder_billing_month = Month.objects.get_from_date(self.date)
            self.save()

    def set_default_project_billing_month(self):
        if not self.project_billing_month:
            self.project_billing_month = Month.objects.get_from_date(self.date)
            self.save()


class Coder:
    def __init__(self, user):
        self.user = user

        self.projects = list(self._iter_projects())
        if not self.projects:
            # for now, a coder should have projects, always
            raise NoCoderProjects()

    def __unicode__(self):
        return self.user.username

    def _iter_projects(self):
        for group in self.user.groups.all():
            for project in group.customer_projects.all():
                yield project

    @staticmethod
    def get(*args, **kwargs):
        user = User.objects.get(*args, **kwargs)
        return Coder(user)

    @staticmethod
    def get_or_none(*args, **kwargs):
        try:
            return Coder.get(*args, **kwargs)
        except NoCoderProjects:
            return None
