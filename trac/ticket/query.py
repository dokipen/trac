# -*- coding: utf-8 -*-
#
# Copyright (C) 2004-2008 Edgewall Software
# Copyright (C) 2004-2005 Christopher Lenz <cmlenz@gmx.de>
# Copyright (C) 2005-2007 Christian Boos <cboos@neuf.fr>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://trac.edgewall.org/wiki/TracLicense.
#
# This software consists of voluntary contributions made by many
# individuals. For the exact contribution history, see the revision
# history and logs, available at http://trac.edgewall.org/log/.
#
# Author: Christopher Lenz <cmlenz@gmx.de>

import csv
from itertools import groupby
from math import ceil
from datetime import datetime, timedelta
import re
from StringIO import StringIO

from genshi.builder import tag

from trac.config import Option, IntOption 
from trac.core import *
from trac.db import get_column_names
from trac.mimeview.api import Mimeview, IContentConverter, Context
from trac.resource import Resource
from trac.ticket.api import TicketSystem
from trac.util import Ranges
from trac.util.datefmt import format_datetime, parse_date, to_timestamp, utc
from trac.util.presentation import Paginator
from trac.util.text import shorten_line
from trac.util.translation import _, tag_
from trac.web import parse_query_string, IRequestHandler
from trac.web.href import Href
from trac.web.chrome import add_ctxtnav, add_link, add_script, add_stylesheet, \
                            INavigationContributor, Chrome

from trac.wiki.api import IWikiSyntaxProvider, parse_args
from trac.wiki.macros import WikiMacroBase # TODO: should be moved in .api

class QuerySyntaxError(Exception):
    """Exception raised when a ticket query cannot be parsed from a string."""


class Query(object):
    substitutions = ['$USER']

    def __init__(self, env, report=None, constraints=None, cols=None,
                 order=None, desc=0, group=None, groupdesc=0, verbose=0,
                 rows=None, page=None, max=None):
        self.env = env
        self.id = report # if not None, it's the corresponding saved query
        self.constraints = constraints or {}
        synonyms = TicketSystem(self.env).get_field_synonyms()
        self.order = synonyms.get(order, order)     # 0.11 compatibility
        self.desc = desc
        self.group = group
        self.groupdesc = groupdesc
        self.default_page = 1
        self.items_per_page = QueryModule(self.env).items_per_page

        # getting page number (default_page if unspecified)
        if not page:
            page = self.default_page
        try:
            self.page = int(page)
            if self.page < 1:
                raise ValueError()
        except ValueError:
            raise TracError(_('Query page %(page)s is invalid.', page=page))

        # max=0 signifies showing all items on one page
        # max=n will show precisely n items on all pages except the last
        # max<0 is invalid
        if max in ('none', ''):
            max = 0

        if max is None: # meaning unspecified
            max = self.items_per_page
        try:
            self.max = int(max)
            if self.max < 0:
                raise ValueError()
        except ValueError:
            raise TracError(_('Query max %(max)s is invalid.', max=max))
        
        if self.max == 0:
            self.has_more_pages = False
            self.offset = 0
        else:
            self.has_more_pages = True
            self.offset = self.max * (self.page - 1)

        if rows == None:
            rows = []
        if verbose and 'description' not in rows: # 0.10 compatibility
            rows.append('description')
        self.fields = TicketSystem(self.env).get_ticket_fields()
        self.time_fields = [f['name'] for f in self.fields
                            if f['type'] == 'time']
        field_names = [f['name'] for f in self.fields]
        self.cols = [c for c in cols or [] if c in field_names or 
                     c == 'id']
        self.rows = [c for c in rows if c in field_names]
        if self.order != 'id' and self.order not in field_names:
            self.order = 'priority'

        if self.group not in field_names:
            self.group = None

    @classmethod
    def from_string(cls, env, string, **kw):
        filters = string.split('&')
        kw_strs = ['order', 'group', 'page', 'max']
        kw_arys = ['rows']
        kw_bools = ['desc', 'groupdesc', 'verbose']
        synonyms = TicketSystem(env).get_field_synonyms()
        constraints = {}
        cols = []
        for filter_ in filters:
            filter_ = filter_.split('=')
            if len(filter_) != 2:
                raise QuerySyntaxError(_('Query filter requires field and ' 
                                         'constraints separated by a "="'))
            field, values = filter_
            # from last chars of `field`, get the mode of comparison
            mode = ''
            if field and field[-1] in ('~', '^', '$') \
                                and not field in cls.substitutions:
                mode = field[-1]
                field = field[:-1]
            if field and field[-1] == '!':
                mode = '!' + mode
                field = field[:-1]
            if not field:
                raise QuerySyntaxError(_('Query filter requires field name'))
            processed_values = []
            for val in values.split('|'):
                val = mode + val # add mode of comparison
                processed_values.append(val)
            try:
                if isinstance(field, unicode):
                    field = field.encode('utf-8')
                if field in kw_strs:
                    kw[field] = processed_values[0]
                elif field in kw_arys:
                    kw[field] = processed_values
                elif field in kw_bools:
                    kw[field] = True
                elif field == 'col':
                    cols.extend(synonyms.get(value, value)
                                for value in processed_values)
                else:
                    constraints.setdefault(synonyms.get(field, field), 
                                           []).extend(processed_values)
            except UnicodeError:
                pass # field must be a str, see `get_href()`
        report = constraints.pop('report', None)
        report = kw.pop('report', report)
        return cls(env, report, constraints=constraints, cols=cols, **kw)

    def get_columns(self):
        if not self.cols:
            self.cols = self.get_default_columns()
        if not 'id' in self.cols:
            # make sure 'id' is always present (needed for permission checks)
            self.cols.insert(0, 'id')        
        return self.cols

    def get_all_textareas(self):
        return [f['name'] for f in self.fields if f['type'] == 'textarea']

    def get_all_columns(self):
        # Prepare the default list of columns
        cols = ['id']
        cols += [f['name'] for f in self.fields if f['type'] != 'textarea']
        for col in ('reporter', 'keywords', 'cc'):
            if col in cols:
                cols.remove(col)
                cols.append(col)

        def sort_columns(col1, col2):
            constrained_fields = self.constraints.keys()
            if 'id' in (col1, col2):
                # Ticket ID is always the first column
                return col1 == 'id' and -1 or 1
            elif 'summary' in (col1, col2):
                # Ticket summary is always the second column
                return col1 == 'summary' and -1 or 1
            elif col1 in constrained_fields or col2 in constrained_fields:
                # Constrained columns appear before other columns
                return col1 in constrained_fields and -1 or 1
            return 0
        cols.sort(sort_columns)
        return cols

    def get_default_columns(self):
        cols = self.get_all_columns()
        
        # Semi-intelligently remove columns that are restricted to a single
        # value by a query constraint.
        for col in [k for k in self.constraints.keys()
                    if k != 'id' and k in cols]:
            constraint = self.constraints[col]
            if len(constraint) == 1 and constraint[0] \
                    and not constraint[0][0] in '!~^$' and col in cols \
                    and col not in self.time_fields:
                cols.remove(col)
            if col == 'status' and not 'closed' in constraint \
                    and 'resolution' in cols:
                cols.remove('resolution')
        if self.group in cols:
            cols.remove(self.group)

        # Only display the first seven columns by default
        cols = cols[:7]
        # Make sure the column we order by is visible, if it isn't also
        # the column we group by
        if not self.order in cols and not self.order == self.group:
            cols[-1] = self.order
        return cols

    def count(self, req, db=None, cached_ids=None):
        sql, args = self.get_sql(req, cached_ids)
        return self._count(sql, args)

    def _count(self, sql, args, db=None):
        if not db:
            db = self.env.get_db_cnx()
        cursor = db.cursor()

        count_sql = 'SELECT COUNT(*) FROM (' + sql + ') AS foo'
        # self.env.log.debug("Count results in Query SQL: " + count_sql % 
        #                    tuple([repr(a) for a in args]))

        cnt = 0
        try:
            cursor.execute(count_sql, args);
        except:
            db.rollback()
            raise
        for cnt, in cursor:
            break
        self.env.log.debug("Count results in Query: %d" % cnt)
        return cnt

    def execute(self, req, db=None, cached_ids=None):
        if not db:
            db = self.env.get_db_cnx()
        cursor = db.cursor()

        sql, args = self.get_sql(req, cached_ids)
        self.num_items = self._count(sql, args, db)

        if self.num_items <= self.max:
            self.has_more_pages = False

        if self.has_more_pages:
            max = self.max
            if self.group:
                max += 1
            sql = sql + " LIMIT %d OFFSET %d" % (max, self.offset)
            if (self.page > int(ceil(float(self.num_items) / self.max)) and
                self.num_items != 0):
                raise TracError(_('Page %(page)s is beyond the number of '
                                  'pages in the query', page=self.page))

        self.env.log.debug("Query SQL: " + sql % tuple([repr(a) for a in args]))     
        try:
            cursor.execute(sql, args)
        except:
            db.rollback()
            raise
        columns = get_column_names(cursor)
        fields = []
        for column in columns:
            fields += [f for f in self.fields if f['name'] == column] or [None]
        results = []

        column_indices = range(len(columns))
        for row in cursor:
            result = {}
            for i in column_indices:
                name, field, val = columns[i], fields[i], row[i]
                if name == self.group:
                    val = val or 'None'
                elif name == 'reporter':
                    val = val or 'anonymous'
                elif name == 'id':
                    val = int(val)
                    result['href'] = req.href.ticket(val)
                elif val is None:
                    val = '--'
                elif name in self.time_fields:
                    val = datetime.fromtimestamp(int(val or 0), utc)
                elif field and field['type'] == 'checkbox':
                    try:
                        val = bool(int(val))
                    except TypeError, ValueError:
                        val = False
                result[name] = val
            results.append(result)
        cursor.close()
        return results

    def get_href(self, href, id=None, order=None, desc=None, format=None,
                 max=None, page=None):
        """Create a link corresponding to this query.

        :param href: the `Href` object used to build the URL
        :param id: optionally set or override the report `id`
        :param order: optionally override the order parameter of the query
        :param desc: optionally override the desc parameter
        :param format: optionally override the format of the query
        :param max: optionally override the max items per page
        :param page: optionally specify which page of results (defaults to
                     the first)

        Note: `get_resource_url` of a 'query' resource?
        """
        if not isinstance(href, Href):
            href = href.href # compatibility with the `req` of the 0.10 API

        if format == 'rss':
            max = self.items_per_page
            page = self.default_page

        if id is None:
            id = self.id
        if desc is None:
            desc = self.desc
        if order is None:
            order = self.order
        if max is None:
            max = self.max
        if page is None:
            page = self.page

        cols = self.get_columns()
        # don't specify the columns in the href if they correspond to
        # the default columns, page and max in the same order. That keeps the
        # query url shorter in the common case where we just want the default
        # columns.
        if cols == self.get_default_columns():
            cols = None
        if page == self.default_page:
            page = None
        if max == self.items_per_page:
            max = None

        return href.query(report=id,
                          order=order, desc=desc and 1 or None,
                          group=self.group or None,
                          groupdesc=self.groupdesc and 1 or None,
                          col=cols,
                          row=self.rows,
                          max=max,
                          page=page,
                          format=format, **self.constraints)

    def to_string(self):
        """Return a user readable and editable representation of the query.

        Note: for now, this is an "exploded" query href, but ideally should be
        expressed in TracQuery language.
        """
        query_string = self.get_href(Href(''))
        if query_string and '?' in query_string:
            query_string = query_string.split('?', 1)[1]
        return 'query:?' + query_string.replace('&', '\n&\n')

    def get_sql(self, req=None, cached_ids=None):
        """Return a (sql, params) tuple for the query."""
        self.get_columns()

        enum_columns = ('resolution', 'priority', 'severity')
        # Build the list of actual columns to query
        cols = self.cols[:]
        def add_cols(*args):
            for col in args:
                if not col in cols:
                    cols.append(col)
        if self.group and not self.group in cols:
            add_cols(self.group)
        if self.rows:
            add_cols('reporter', *self.rows)
        add_cols('status', 'priority', 'time', 'changetime', self.order)
        cols.extend([c for c in self.constraints.keys() if not c in cols])

        custom_fields = [f['name'] for f in self.fields if 'custom' in f]

        sql = []
        sql.append("SELECT " + ",".join(['t.%s AS %s' % (c, c) for c in cols
                                         if c not in custom_fields]))
        sql.append(",priority.value AS priority_value")
        for k in [k for k in cols if k in custom_fields]:
            sql.append(",%s.value AS %s" % (k, k))
        sql.append("\nFROM ticket AS t")

        # Join with ticket_custom table as necessary
        for k in [k for k in cols if k in custom_fields]:
           sql.append("\n  LEFT OUTER JOIN ticket_custom AS %s ON " \
                      "(id=%s.ticket AND %s.name='%s')" % (k, k, k, k))

        # Join with the enum table for proper sorting
        for col in [c for c in enum_columns
                    if c == self.order or c == self.group or c == 'priority']:
            sql.append("\n  LEFT OUTER JOIN enum AS %s ON "
                       "(%s.type='%s' AND %s.name=%s)"
                       % (col, col, col, col, col))

        # Join with the version/milestone tables for proper sorting
        for col in [c for c in ['milestone', 'version']
                    if c == self.order or c == self.group]:
            sql.append("\n  LEFT OUTER JOIN %s ON (%s.name=%s)"
                       % (col, col, col))

        def get_constraint_sql(name, value, mode, neg):
            if name not in custom_fields:
                col = 't.' + name
            else:
                col = name + '.value'
            value = value[len(mode) + neg:]

            if name in self.time_fields:
                if ';' in value:
                    (start, end) = [each.strip() for each in 
                                    value.split(';', 1)]
                else:
                    (start, end) = (value.strip(), '')
                col_cast = db.cast(col, 'int')
                if start and end:
                    start = to_timestamp(parse_date(start, req.tz))
                    end = to_timestamp(parse_date(end, req.tz))
                    return ("%s(%s>=%%s AND %s<%%s)" % (neg and 'NOT ' or '',
                                                        col_cast, col_cast),
                            (start, end))
                elif start:
                    start = to_timestamp(parse_date(start, req.tz))
                    return ("%s%s>=%%s" % (neg and 'NOT ' or '', col_cast),
                            (start, ))
                elif end:
                    end = to_timestamp(parse_date(end, req.tz))
                    return ("%s%s<%%s" % (neg and 'NOT ' or '', col_cast),
                            (end, ))
                else:
                    return None
                
            if mode == '':
                return ("COALESCE(%s,'')%s=%%s" % (col, neg and '!' or ''),
                        (value, ))

            if not value:
                return None
            value = db.like_escape(value)
            if mode == '~':
                value = '%' + value + '%'
            elif mode == '^':
                value = value + '%'
            elif mode == '$':
                value = '%' + value
            return ("COALESCE(%s,'') %s%s" % (col, neg and 'NOT ' or '',
                                              db.like()),
                    (value, ))

        db = self.env.get_db_cnx()
        clauses = []
        args = []
        for k, v in self.constraints.items():
            if req:
                v = [val.replace('$USER', req.authname) for val in v]
            # Determine the match mode of the constraint (contains,
            # starts-with, negation, etc.)
            neg = v[0].startswith('!')
            mode = ''
            if len(v[0]) > neg and v[0][neg] in ('~', '^', '$'):
                mode = v[0][neg]

            # Special case id ranges
            if k == 'id':
                ranges = Ranges()
                for r in v:
                    r = r.replace('!', '')
                    ranges.appendrange(r)
                ids = []
                id_clauses = []
                for a,b in ranges.pairs:
                    if a == b:
                        ids.append(str(a))
                    else:
                        id_clauses.append('id BETWEEN %s AND %s')
                        args.append(a)
                        args.append(b)
                if ids:
                    id_clauses.append('id IN (%s)' % (','.join(ids)))
                if id_clauses:
                    clauses.append('%s(%s)' % (neg and 'NOT ' or '',
                                               ' OR '.join(id_clauses)))
            # Special case for exact matches on multiple values
            elif not mode and len(v) > 1 and k not in self.time_fields:
                if k not in custom_fields:
                    col = 't.' + k
                else:
                    col = k + '.value'
                clauses.append("COALESCE(%s,'') %sIN (%s)"
                               % (col, neg and 'NOT ' or '',
                                  ','.join(['%s' for val in v])))
                args += [val[neg:] for val in v]
            elif len(v) > 1:
                constraint_sql = filter(None,
                                        [get_constraint_sql(k, val, mode, neg)
                                         for val in v])
                if not constraint_sql:
                    continue
                if neg:
                    clauses.append("(" + " AND ".join(
                        [item[0] for item in constraint_sql]) + ")")
                else:
                    clauses.append("(" + " OR ".join(
                        [item[0] for item in constraint_sql]) + ")")
                for item in constraint_sql:
                    args.extend(item[1])
            elif len(v) == 1:
                constraint_sql = get_constraint_sql(k, v[0], mode, neg)
                if constraint_sql:
                    clauses.append(constraint_sql[0])
                    args.extend(constraint_sql[1])

        clauses = filter(None, clauses)
        if clauses:
            sql.append("\nWHERE ")
            sql.append(" AND ".join(clauses))
            if cached_ids:
                sql.append(" OR ")
                sql.append("id in (%s)" % (','.join(
                                                [str(id) for id in cached_ids])))
            
        sql.append("\nORDER BY ")
        order_cols = [(self.order, self.desc)]
        if self.group and self.group != self.order:
            order_cols.insert(0, (self.group, self.groupdesc))
        for name, desc in order_cols:
            if name in custom_fields or name in enum_columns:
                col = name + '.value'
            else:
                col = 't.' + name
            desc = desc and ' DESC' or ''
            # FIXME: This is a somewhat ugly hack.  Can we also have the
            #        column type for this?  If it's an integer, we do first
            #        one, if text, we do 'else'
            if name == 'id' or name in self.time_fields:
                sql.append("COALESCE(%s,0)=0%s," % (col, desc))
            else:
                sql.append("COALESCE(%s,'')=''%s," % (col, desc))
            if name in enum_columns:
                # These values must be compared as ints, not as strings
                db = self.env.get_db_cnx()
                sql.append(db.cast(col, 'int') + desc)
            elif name == 'milestone':
                sql.append("COALESCE(milestone.completed,0)=0%s,"
                           "milestone.completed%s,"
                           "COALESCE(milestone.due,0)=0%s,milestone.due%s,"
                           "%s%s" % (desc, desc, desc, desc, col, desc))
            elif name == 'version':
                sql.append("COALESCE(version.time,0)=0%s,version.time%s,%s%s"
                           % (desc, desc, col, desc))
            else:
                sql.append("%s%s" % (col, desc))
            if name == self.group and not name == self.order:
                sql.append(",")
        if self.order != 'id':
            sql.append(",t.id")  

        return "".join(sql), args

    def template_data(self, context, tickets, orig_list=None, orig_time=None,
                      req=None):
        constraints = {}
        for k, v in self.constraints.items():
            constraint = {'values': [], 'mode': ''}
            for val in v:
                neg = val.startswith('!')
                if neg:
                    val = val[1:]
                mode = ''
                if val[:1] in ('~', '^', '$') \
                                    and not val in self.substitutions:
                    mode, val = val[:1], val[1:]
                constraint['mode'] = (neg and '!' or '') + mode
                constraint['values'].append(val)
            constraints[k] = constraint

        cols = self.get_columns()
        labels = dict([(f['name'], f['label']) for f in self.fields])
        wikify = set(f['name'] for f in self.fields 
                     if f['type'] == 'text' and f.get('format') == 'wiki')

        headers = [{
            'name': col, 'label': labels.get(col, _('Ticket')),
            'wikify': col in wikify,
            'href': self.get_href(context.href, order=col,
                                  desc=(col == self.order and not self.desc))
        } for col in cols]

        fields = {}
        for field in self.fields:
            field_data = {}
            field_data.update(field)
            del field_data['name']
            fields[field['name']] = field_data

        modes = {}
        modes['text'] = [
            {'name': _("contains"), 'value': "~"},
            {'name': _("doesn't contain"), 'value': "!~"},
            {'name': _("begins with"), 'value': "^"},
            {'name': _("ends with"), 'value': "$"},
            {'name': _("is"), 'value': ""},
            {'name': _("is not"), 'value': "!"}
        ]
        modes['textarea'] = [
            {'name': _("contains"), 'value': "~"},
            {'name': _("doesn't contain"), 'value': "!~"},
        ]
        modes['select'] = [
            {'name': _("is"), 'value': ""},
            {'name': _("is not"), 'value': "!"}
        ]

        groups = {}
        groupsequence = []
        for ticket in tickets:
            if orig_list:
                # Mark tickets added or changed since the query was first
                # executed
                if ticket['time'] > orig_time:
                    ticket['added'] = True
                elif ticket['changetime'] > orig_time:
                    ticket['changed'] = True
            if self.group:
                group_key = ticket[self.group]
                groups.setdefault(group_key, []).append(ticket)
                if not groupsequence or group_key not in groupsequence:
                    groupsequence.append(group_key)
        groupsequence = [(value, groups[value]) for value in groupsequence]

        # detect whether the last group continues on the next page,
        # by checking if the extra (max+1)th ticket is in the last group
        last_group_is_partial = False
        if groupsequence and self.max and len(tickets) == self.max + 1:
            del tickets[-1]
            if len(groupsequence[-1][1]) == 1: 
                # additional ticket started a new group
                del groupsequence[-1] # remove that additional group
            else:
                # additional ticket stayed in the group 
                last_group_is_partial = True
                del groupsequence[-1][1][-1] # remove the additional ticket

        results = Paginator(tickets,
                            self.page - 1,
                            self.max,
                            self.num_items)
        
        if req:
            if results.has_next_page:
                next_href = self.get_href(req.href, max=self.max, 
                                          page=self.page + 1)
                add_link(req, 'next', next_href, _('Next Page'))

            if results.has_previous_page:
                prev_href = self.get_href(req.href, max=self.max, 
                                          page=self.page - 1)
                add_link(req, 'prev', prev_href, _('Previous Page'))
        else:
            results.show_index = False

        pagedata = []
        shown_pages = results.get_shown_pages(21)
        for page in shown_pages:
            pagedata.append([self.get_href(context.href, page=page), None,
                             str(page), _('Page %(num)d', num=page)])

        results.shown_pages = [dict(zip(['href', 'class', 'string', 'title'],
                                        p)) for p in pagedata]
        results.current_page = {'href': None, 'class': 'current',
                                'string': str(results.page + 1),
                                'title':None}

        return {'query': self,
                'context': context,
                'col': cols,
                'row': self.rows,
                'constraints': constraints,
                'labels': labels,
                'headers': headers,
                'fields': fields,
                'modes': modes,
                'tickets': tickets,
                'groups': groupsequence or [(None, tickets)],
                'last_group_is_partial': last_group_is_partial,
                'paginator': results}
    
class QueryModule(Component):

    implements(IRequestHandler, INavigationContributor, IWikiSyntaxProvider,
               IContentConverter)
               
    default_query = Option('query', 'default_query',
        default='status!=closed&owner=$USER', 
        doc="""The default query for authenticated users. The query is either
            in [TracQuery#QueryLanguage query language] syntax, or a URL query
            string starting with `?` as used in `query:`
            [TracQuery#UsingTracLinks Trac links].
            (''since 0.11.2'')""") 
    
    default_anonymous_query = Option('query', 'default_anonymous_query',  
        default='status!=closed&cc~=$USER', 
        doc="""The default query for anonymous users. The query is either
            in [TracQuery#QueryLanguage query language] syntax, or a URL query
            string starting with `?` as used in `query:`
            [TracQuery#UsingTracLinks Trac links].
            (''since 0.11.2'')""") 

    items_per_page = IntOption('query', 'items_per_page', 100,
        """Number of tickets displayed per page in ticket queries,
        by default (''since 0.11'')""")

    # IContentConverter methods
    def get_supported_conversions(self):
        yield ('rss', _('RSS Feed'), 'xml',
               'trac.ticket.Query', 'application/rss+xml', 8)
        yield ('csv', _('Comma-delimited Text'), 'csv',
               'trac.ticket.Query', 'text/csv', 8)
        yield ('tab', _('Tab-delimited Text'), 'tsv',
               'trac.ticket.Query', 'text/tab-separated-values', 8)

    def convert_content(self, req, mimetype, query, key):
        if key == 'rss':
            return self.export_rss(req, query)
        elif key == 'csv':
            return self.export_csv(req, query, mimetype='text/csv')
        elif key == 'tab':
            return self.export_csv(req, query, '\t',
                                   mimetype='text/tab-separated-values')

    # INavigationContributor methods

    def get_active_navigation_item(self, req):
        return 'tickets'

    def get_navigation_items(self, req):
        from trac.ticket.report import ReportModule
        if 'TICKET_VIEW' in req.perm and \
                not self.env.is_component_enabled(ReportModule):
            yield ('mainnav', 'tickets',
                   tag.a(_('View Tickets'), href=req.href.query()))

    # IRequestHandler methods

    def match_request(self, req):
        return req.path_info == '/query'

    def process_request(self, req):
        req.perm.assert_permission('TICKET_VIEW')

        constraints = self._get_constraints(req)
        args = req.args
        if not constraints and not 'order' in req.args:
            # If no constraints are given in the URL, use the default ones.
            if req.authname and req.authname != 'anonymous':
                qstring = self.default_query
                user = req.authname
            else:
                email = req.session.get('email')
                name = req.session.get('name')
                qstring = self.default_anonymous_query
                user = email or name or None
                      
            self.log.debug('QueryModule: Using default query: %s', str(qstring))
            if qstring.startswith('?'):
                ticket_fields = [f['name'] for f in
                                 TicketSystem(self.env).get_ticket_fields()]
                ticket_fields.append('id')
                args = parse_query_string(qstring[1:])
                constraints = dict([(k, args.getlist(k)) for k in args 
                                    if k in ticket_fields])
            else:
                constraints = Query.from_string(self.env, qstring).constraints
                # Substitute $USER, or ensure no field constraints that depend
                # on $USER are used if we have no username.
                for field, vals in constraints.items():
                    for (i, val) in enumerate(vals):
                        if user:
                            vals[i] = val.replace('$USER', user)
                        elif val.endswith('$USER'):
                            del constraints[field]
                            break

        cols = args.get('col')
        if isinstance(cols, basestring):
            cols = [cols]
        # Since we don't show 'id' as an option to the user,
        # we need to re-insert it here.            
        if cols and 'id' not in cols: 
            cols.insert(0, 'id')
        rows = args.get('row', [])
        if isinstance(rows, basestring):
            rows = [rows]
        format = req.args.get('format')
        max = args.get('max')
        if max is None and format in ('csv', 'tab'):
            max = 0 # unlimited unless specified explicitly
        query = Query(self.env, req.args.get('report'),
                      constraints, cols, args.get('order'),
                      'desc' in args, args.get('group'),
                      'groupdesc' in args, 'verbose' in args,
                      rows,
                      args.get('page'), 
                      max)

        if 'update' in req.args:
            # Reset session vars
            for var in ('query_constraints', 'query_time', 'query_tickets'):
                if var in req.session:
                    del req.session[var]
            req.redirect(query.get_href(req.href))

        # Add registered converters
        for conversion in Mimeview(self.env).get_supported_conversions(
                                             'trac.ticket.Query'):
            add_link(req, 'alternate',
                     query.get_href(req.href, format=conversion[0]),
                     conversion[1], conversion[4], conversion[0])

        if format:
            Mimeview(self.env).send_converted(req, 'trac.ticket.Query', query,
                                              format, filename=None)

        return self.display_html(req, query)

    # Internal methods

    def _get_constraints(self, req):
        constraints = {}
        fields = TicketSystem(self.env).get_ticket_fields()
        synonyms = TicketSystem(self.env).get_field_synonyms()
        ticket_fields = [f['name'] for f in fields]
        ticket_fields.append('id')
        ticket_fields.extend(synonyms.iterkeys())
        time_fields = [f['name'] for f in fields if f['type'] == 'time']
        time_fields.extend([k for (k, v) in synonyms.iteritems() 
                            if v in time_fields])

        # For clients without JavaScript, we remove constraints here if
        # requested
        remove_constraints = {}
        to_remove = [k[10:] for k in req.args.keys()
                     if k.startswith('rm_filter_')]
        if to_remove: # either empty or containing a single element
            match = re.match(r'(\w+?)_(\d+)$', to_remove[0])
            if match:
                remove_constraints[match.group(1)] = int(match.group(2))
            else:
                remove_constraints[to_remove[0]] = -1

        for field in [k for k in req.args.keys() if k in ticket_fields]:
            vals = req.args[field]
            if not isinstance(vals, (list, tuple)):
                vals = [vals]
            if vals:
                mode = req.args.get(field + '_mode')
                if mode:
                    vals = [mode + x for x in vals]
                if field in time_fields:
                    ends = req.args.getlist(field + '_end')
                    if ends:
                        vals = [start + ';' + end 
                                for (start, end) in zip(vals, ends)]
                if field in remove_constraints:
                    idx = remove_constraints[field]
                    if idx >= 0:
                        del vals[idx]
                        if not vals:
                            continue
                    else:
                        continue
                constraints.setdefault(synonyms.get(field, field), 
                                       []).extend(vals)

        return constraints

    def display_html(self, req, query):
        db = self.env.get_db_cnx()

        # The most recent query is stored in the user session;
        orig_list = None
        orig_time = datetime.now(utc)
        query_time = int(req.session.get('query_time', 0))
        query_time = datetime.fromtimestamp(query_time, utc)
        query_constraints = unicode(query.constraints)
        if query_constraints != req.session.get('query_constraints') \
                or query_time < orig_time - timedelta(hours=1):
            tickets = query.execute(req, db)
            # New or outdated query, (re-)initialize session vars
            req.session['query_constraints'] = query_constraints
            req.session['query_tickets'] = ' '.join([str(t['id'])
                                                     for t in tickets])
        else:
            orig_list = [int(id) for id
                         in req.session.get('query_tickets', '').split()]
            tickets = query.execute(req, db, orig_list)
            orig_time = query_time

        context = Context.from_request(req, 'query')
        owner_field = [f for f in query.fields if f['name'] == 'owner']
        if owner_field:
            TicketSystem(self.env).eventually_restrict_owner(owner_field[0])
        data = query.template_data(context, tickets, orig_list, orig_time, req)

        # For clients without JavaScript, we add a new constraint here if
        # requested
        constraints = data['constraints']
        if 'add' in req.args:
            field = req.args.get('add_filter')
            if field:
                constraint = constraints.setdefault(field, {})
                constraint.setdefault('values', []).append('')
                # FIXME: '' not always correct (e.g. checkboxes)

        req.session['query_href'] = query.get_href(context.href)
        req.session['query_time'] = to_timestamp(orig_time)
        req.session['query_tickets'] = ' '.join([str(t['id'])
                                                 for t in tickets])
        title = _('Custom Query')

        # Only interact with the report module if it is actually enabled.
        #
        # Note that with saved custom queries, there will be some convergence
        # between the report module and the query module.
        from trac.ticket.report import ReportModule
        if 'REPORT_VIEW' in req.perm and \
               self.env.is_component_enabled(ReportModule):
            data['report_href'] = req.href.report()
            add_ctxtnav(req, _('Available Reports'), req.href.report())
            add_ctxtnav(req, _('Custom Query'))
            if query.id:
                cursor = db.cursor()
                cursor.execute("SELECT title,description FROM report "
                               "WHERE id=%s", (query.id,))
                for title, description in cursor:
                    data['report_resource'] = Resource('report', query.id)
                    data['description'] = description
        else:
            data['report_href'] = None
        data.setdefault('report', None)
        data.setdefault('description', None)
        data['title'] = title

        data['all_columns'] = query.get_all_columns()
        # Don't allow the user to remove the id column        
        data['all_columns'].remove('id')
        data['all_textareas'] = query.get_all_textareas()

        add_stylesheet(req, 'common/css/report.css')
        add_script(req, 'common/js/query.js')

        return 'query.html', data, None

    def export_csv(self, req, query, sep=',', mimetype='text/plain'):
        content = StringIO()
        cols = query.get_columns()
        writer = csv.writer(content, delimiter=sep)
        writer = csv.writer(content, delimiter=sep, quoting=csv.QUOTE_MINIMAL)
        writer.writerow([unicode(c).encode('utf-8') for c in cols])

        context = Context.from_request(req)
        results = query.execute(req, self.env.get_db_cnx())
        for result in results:
            ticket = Resource('ticket', result['id'])
            if 'TICKET_VIEW' in req.perm(ticket):
                values = []
                for col in cols:
                    value = result[col]
                    if col in ('cc', 'reporter'):
                        value = Chrome(self.env).format_emails(context(ticket),
                                                               value)
                    elif col in query.time_fields:
                        value = format_datetime(value, tzinfo=req.tz)
                    values.append(unicode(value).encode('utf-8'))
                writer.writerow(values)
        return (content.getvalue(), '%s;charset=utf-8' % mimetype)

    def export_rss(self, req, query):
        if 'description' not in query.rows:
            query.rows.append('description')
        db = self.env.get_db_cnx()
        results = query.execute(req, db)
        query_href = req.abs_href.query(group=query.group,
                                        groupdesc=(query.groupdesc and 1
                                                   or None),
                                        row=query.rows,
                                        page=req.args.get('page'), 
                                        max=req.args.get('max'),
                                        **query.constraints)
        data = {
            'context': Context.from_request(req, 'query', absurls=True),
            'results': results,
            'query_href': query_href
        }
        output = Chrome(self.env).render_template(req, 'query.rss', data,
                                                  'application/rss+xml')
        return output, 'application/rss+xml'

    # IWikiSyntaxProvider methods
    
    def get_wiki_syntax(self):
        return []
    
    def get_link_resolvers(self):
        yield ('query', self._format_link)

    def _format_link(self, formatter, ns, query, label):
        if query.startswith('?'):
            return tag.a(label, class_='query',
                         href=formatter.href.query() + query.replace(' ', '+'))
        else:
            try:
                query = Query.from_string(self.env, query)
                return tag.a(label,
                             href=query.get_href(formatter.context.href),
                             class_='query')
            except QuerySyntaxError, e:
                return tag.em(_('[Error: %(error)s]', error=e), class_='error')


class TicketQueryMacro(WikiMacroBase):
    """Macro that lists tickets that match certain criteria.
    
    This macro accepts a comma-separated list of keyed parameters,
    in the form "key=value".

    If the key is the name of a field, the value must use the syntax 
    of a filter specifier as defined in TracQuery#QueryLanguage.
    Note that this is ''not'' the same as the simplified URL syntax 
    used for `query:` links starting with a `?` character.

    In addition to filters, several other named parameters can be used
    to control how the results are presented. All of them are optional.

    The `format` parameter determines how the list of tickets is
    presented: 
     - '''list''' -- the default presentation is to list the ticket ID next
       to the summary, with each ticket on a separate line.
     - '''compact''' -- the tickets are presented as a comma-separated
       list of ticket IDs. 
     - '''count''' -- only the count of matching tickets is displayed
     - '''table'''  -- a view similar to the custom query view (but without
       the controls)

    The `max` parameter can be used to limit the number of tickets shown
    (defaults to '''0''', i.e. no maximum).

    The `order` parameter sets the field used for ordering tickets
    (defaults to '''id''').

    The `desc` parameter indicates whether the order of the tickets
    should be reversed (defaults to '''false''').

    The `group` parameter sets the field used for grouping tickets
    (defaults to not being set).

    The `groupdesc` parameter indicates whether the natural display
    order of the groups should be reversed (defaults to '''false''').

    The `verbose` parameter can be set to a true value in order to
    get the description for the listed tickets. For '''table''' format only.
    ''deprecated in favor of the `rows` parameter''
    
    The `rows` parameter can be used to specify which field(s) should 
    be viewed as a row, e.g. `rows=description|summary`

    For compatibility with Trac 0.10, if there's a second positional parameter
    given to the macro, it will be used to specify the `format`.
    Also, using "&" as a field separator still works but is deprecated.
    """

    def expand_macro(self, formatter, name, content):
        req = formatter.req
        query_string = ''
        argv, kwargs = parse_args(content, strict=False)
        if len(argv) > 0 and not 'format' in kwargs: # 0.10 compatibility hack
            kwargs['format'] = argv[0]

        if 'order' not in kwargs:
            kwargs['order'] = 'id'
        if 'max' not in kwargs:
            kwargs['max'] = '0' # unlimited by default

        format = kwargs.pop('format', 'list').strip().lower()
        if format in ('list', 'compact'): # we need 'status' and 'summary'
            kwargs['col'] = '|'.join(['status', 'summary', 
                                      kwargs.get('col', '')])

        query_string = '&'.join(['%s=%s' % item
                                 for item in kwargs.iteritems()])
        query = Query.from_string(self.env, query_string)

        if format == 'count':
            cnt = query.count(req)
            return tag.span(cnt, title='%d tickets for which %s' %
                            (cnt, query_string), class_='query_count')
        
        tickets = query.execute(req)

        if format == 'table':
            data = query.template_data(formatter.context, tickets)

            add_stylesheet(req, 'common/css/report.css')
            
            return Chrome(self.env).render_template(
                req, 'query_results.html', data, None, fragment=True)

        # 'table' format had its own permission checks, here we need to
        # do it explicitly:

        tickets = [t for t in tickets 
                   if 'TICKET_VIEW' in req.perm('ticket', t['id'])]

        if not tickets:
            return tag.span(_("No results"), class_='query_no_results')

        def ticket_anchor(ticket):
            return tag.a('#%s' % ticket['id'],
                         class_=ticket['status'],
                         href=req.href.ticket(int(ticket['id'])),
                         title=shorten_line(ticket['summary']))
        def ticket_groups():
            groups = []
            for v, g in groupby(tickets, lambda t: t[query.group]):
                q = Query.from_string(self.env, query_string)
                # produce the hint for the group
                q.group = q.groupdesc = None
                order = q.order
                q.order = None
                title = "%s %s tickets matching %s" % (v, query.group,
                                                       q.to_string())
                # produce the href for the query corresponding to the group
                q.constraints[str(query.group)] = v
                q.order = order
                href = q.get_href(formatter.context)
                groups.append((v, [t for t in g], href, title))
            return groups

        if format == 'compact':
            if query.group:
                groups = [tag.a('#%s' % ','.join([str(t['id'])
                                                  for t in g]),
                                href=href, class_='query', title=title)
                          for v, g, href, title in ticket_groups()]
                return tag(groups[0], [(', ', g) for g in groups[1:]])
            else:
                alist = [ticket_anchor(ticket) for ticket in tickets]
                return tag.span(alist[0], *[(', ', a) for a in alist[1:]])
        else:
            if query.group:
                return tag.div(
                    [(tag.p(tag_('%(groupvalue)s %(groupname)s tickets:',
                                 groupvalue=tag.a(v, href=href, class_='query',
                                                  title=title),
                                 groupname=query.group)),
                      tag.dl([(tag.dt(ticket_anchor(t)),
                               tag.dd(t['summary'])) for t in g],
                             class_='wiki compact'))
                     for v, g, href, title in ticket_groups()])
            else:
                return tag.div(tag.dl([(tag.dt(ticket_anchor(ticket)),
                                        tag.dd(ticket['summary']))
                                       for ticket in tickets],
                                      class_='wiki compact'))
