import difflib

from django.db import models
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe
from django import forms
from django.conf import settings
from django.contrib.gis.db import models as gis_models

import diff_match_patch
import daisydiff
from versionutils.versioning.utils import is_historical_instance


class DiffUtilNotFound(Exception):
    """
    No appropriate diff util registered for this object.
    """
    pass


class BaseFieldDiff():
    """
    Simplest diff possible, used when no better option is available.
    Just shows two fields side-by-side, in the case of HTML output.
    To customize how two fields get compared, subclass BaseFieldDiff,
    implement (at minimum) get_diff() and as_html() and register your
    class either for a certain field type or for a specific field of a
    specific model.  See BaseModelDiff for details on the latter.

    Here's how you might create your own diff util and register it for
    a field type::

        class MyIntegerDiff(BaseFieldDiff):
            def get_diff(self):
                if self.field1 == self.field2:
                    return None
                return {'difference': (self.field2 - self.field1)}

            def as_html(self):
                d = self.get_diff()
                if d is None:
                    return 'Values are the same'
                if d['difference'] > 0:
                    return 'New value is larger'
                return 'New value is smaller'
                Unchanged

        diff.register(models.IntegerField, MyIntegerDiff)

    Attributes:
        field1: The first value you want to diff.
        field2: The second value you want to diff, against field1.
        template: An optional filename of the template to use when rendering.
    """
    template = None

    def __init__(self, field1, field2):
        """
        Args:
            field1: The first value you want to diff.
            field2: The second value you want to diff, against field1.
        """
        self.field1 = field1
        self.field2 = field2

    def get_diff(self):
        """
        Compares the two field values (field1 and field2).

        Returns:
            A dictionary containing the diff information.  This dictionary
            can then be used by as_html() for rendering HTML.  Returns None
            if the field values are equal.
        """
        if self.field1 == self.field2:
            return None
        return {'deleted': self.field1, 'inserted': self.field2}

    def as_dict(self):
        """
        Returns:
            The diff as a dictionary or array of dictionaries.
        """
        return self.get_diff()

    def as_html(self):
        """
        Returns:
            A string of the diff between field1 and field2.  These are normally
            one row of an HTML table, with two cells in a side-by-side diff.
        """
        diff = self.as_dict()
        if self.template:
            return render_to_string(self.template, diff)

        if diff is None:
            return '<tr><td colspan="2">(No differences found)</td></tr>'
        return '<tr><td>%s</td><td>%s</td></tr>' % (self.field1, self.field2)

    def _media(self):
        return forms.Media()
    media = property(_media)

    def __str__(self):
        return self.as_html()

    def __unicode__(self):
        return mark_safe(unicode(self.__str__()))


class BaseModelDiff(object):
    """
    Diff util used for comparing two model instances.  By default, it will
    compare the instances on all of their fields, except AutoFields.
    To customize which fields to compare, subclass BaseModelDiff, setting the
    'fields' or 'excludes' attributes like this::

        class MyModelDiff(diff.BaseModelDiff):
            fields =   ('name',
                        'contents')

    and then register it with your model like this::

        diff.register(MyModel, MyModelDiff)

    You can also customize how each field is compared by using a tuple
    ('field_name', FieldDiff) instead of just the field names, like this::

        class MyModelDiff(diff.BaseModelDiff):
            fields =   ( ('name', diff.TextFieldDiff),
                         ('contents', diff.HtmlFieldDiff),
                       )

    Attributes:
        fields: An optional tuple containing either field names or
            ('field_name', FieldDiff).  The listed fields will be
            diffed.
        excludes: An optional tuple of field names to ignore.

    """
    fields = None
    excludes = ()

    def __init__(self, model1, model2):
        """
        Args:
            model1: The first model instance you want to diff.
            model2: The second model instance you want to diff,
                against model1.
        """
        self.model1 = model1
        self.model2 = model2
        self._diff = None

    def as_dict(self):
        """
        Returns:
            The diff as a dictionary or array of dictionaries.
        """
        diffs = {}
        for field, field_diff in self.get_diff().items():
            field_diff_dict = field_diff.as_dict()
            if field_diff_dict:
                diffs[field] = field_diff_dict
        if not diffs:
            diffs = None
        return diffs

    def as_html(self):
        """
        Renders the diffs between the model instances as an HTML table. Only
        those fields that are different will be rendered as rows in the
        table. Fields that are not different will be skipped.

        Returns:
            An html string string representing the differences between
            model1 and model2.
        """
        diffs = self.get_diff()
        diff_str = []
        if self.fields:
            display_order = self.fields
        else:
            display_order = diffs.keys()
        for name in display_order:
            if not isinstance(name, basestring):
                name = name[0]
            if diffs[name].get_diff():
                diff_str.append('<tr><td colspan="2">%s</td></tr>' % (name, ))
                diff_str.append('%s' % (diffs[name], ))
        if diff_str:
            return '\n'.join(diff_str)
        return '<tr><td colspan="2">No differences found</td></tr>'

    def get_diff(self):
        """
        Returns:
            A dictionary that contains all field diffs, indexed by field name.
        """
        if self._diff is not None:
            return self._diff
        diff = {}
        diff_utils = {}

        if self.fields:
            diff_fields = self.fields
        else:
            diff_fields = [f.name for f in self.model1._meta.fields]

        for name in diff_fields:
            if isinstance(name, basestring):
                field = self.model1._meta.get_field(name)
                diff_class = registry.get_diff_util(field.__class__)
            else:
                field = self.model1._meta.get_field(name[0])
                diff_class = name[1]
            diff_utils[field] = diff_class

        for field, diff_class in diff_utils.items():
            if not (isinstance(field, (models.AutoField,
                                       ))
                    ):
                if field.name in self.excludes:
                    continue

                diff[field.name] = diff_class(
                    getattr(self.model1, field.name),
                    getattr(self.model2, field.name)
                )

        self._diff = diff
        return diff

    def __getitem__(self, name):
        "Returns a BoundField with the given name."
        d = self.get_diff()
        try:
            field = d[name]
        except KeyError:
            raise KeyError('Key %r not found in Model Diff' % name)
        return field

    def _media(self):
        # Add all the field diffs' media attributes together.
        # Because the field diffs don't compute anything until their
        # relevant get_diff() methods are called, this isn't expensive.
        m = forms.Media()
        for name, field_diff in self.get_diff().iteritems():
            m += field_diff.media
        return m
    media = property(_media)

    def __str__(self):
        return self.as_html()

    def __unicode__(self):
        """
        For use in templates, returns a safe string.
        """
        return mark_safe(unicode(self.__str__()))


class TextFieldDiff(BaseFieldDiff):
    """
    Compares the fields as text and renders the diff in an easy to read format.
    """
    template = 'diff/text_diff.html'

    def as_html(self):
        d = self.get_diff()
        if d is None:
            return '<tr><td colspan="2">(No differences found)</td></tr>'
        return render_to_string(self.template, {'diff': d})

    def get_diff(self):
        return get_diff_operations_clean(self.field1, self.field2)


class HtmlFieldDiff(BaseFieldDiff):
    """
    Compares the fields as HTML and renders them side-by-side, with the
    deleted, inserted, and changed text and elements highlighted.
    To use for a field type, first register in your code like this:
    diff.register(MyHtmlField, diff.HtmlFieldDiff)
    """
    DAISYDIFF_URL = getattr(settings, 'DAISYDIFF_URL', 'http://localhost:8080')

    def as_html(self):
        d = self.get_diff()
        if d is None:
            return '<tr><td colspan="2">(No differences found)</td></tr>'
        try:
            return daisydiff.daisydiff(d['deleted'], d['inserted'],
                                       self.DAISYDIFF_URL)
        except:
            return TextFieldDiff(d['deleted'], d['inserted']).as_html()

    def get_diff(self):
        if self.field1 == self.field2:
            return None
        return {'deleted': self.field1, 'inserted': self.field2}


class FileFieldDiff(BaseFieldDiff):
    """
    Compares the fields as file paths and renders links to the files.
    """
    template = 'diff/file_diff.html'

    def get_diff(self):
        """
        Returns a dictionary of all the file attributes or None if it's the
        same file.
        """
        if self.field1 == self.field2:
            return None

        diff = {
                'name': {'deleted': self.field1.name,
                         'inserted': self.field2.name},
                'url': {'deleted': self.field1.url,
                        'inserted': self.field2.url},
               }
        return diff

    def as_html(self):
        d = self.get_diff()
        if d is None:
            return '<tr><td colspan="2">(No differences found)</td></tr>'
        return render_to_string(self.template, {'diff': d})


class ImageFieldDiff(FileFieldDiff):
    """
    Compares the fields as image file paths and renders the images.
    """
    template = 'diff/image_diff.html'

    def as_html(self):
        d = self.get_diff()
        if d is None:
            return '<tr><td colspan="2">(No differences found)</td></tr>'
        return render_to_string(self.template, {'diff': d})


class GeometryFieldDiff(BaseFieldDiff):
    """
    Compares two Geometry Field types and renders their difference as a
    geometry collection.
    """
    template = 'diff/geometry_diff.html'

    def _split_out_geometry(self, types, geoms):
        from django.contrib.gis.geos import GeometryCollection

        other_geom = []
        split_out = []
        if type(geoms) != GeometryCollection:
            geoms = GeometryCollection(geoms, srid=geoms.srid)
        for geom in geoms:
            if type(geom) in types:
                split_out.append(geom)
            else:
                other_geom.append(geom)
        split_out = GeometryCollection(split_out, srid=geoms.srid)
        other_geom = GeometryCollection(other_geom, srid=geoms.srid)

        return (split_out, other_geom)

    def get_diff(self):
        from django.contrib.gis.geos import Point, MultiPoint, LineString
        from django.contrib.gis.geos import LinearRing, MultiLineString
        from django.contrib.gis.geos import GeometryCollection

        if self.field1 == self.field2:
            return None

        LINE_TYPES = [LineString, LinearRing, MultiLineString]

        # Separate lines from other geometry in field1.
        lines1, other_geom1 = self._split_out_geometry(LINE_TYPES, self.field1)

        # Separate lines from other geometry in field2.
        lines2, other_geom2 = self._split_out_geometry(LINE_TYPES, self.field2)

        # For lines, we do an intersection() and then filter out point
        # intersections.
        lines_same = []
        for geom in lines1.intersection(lines2):
            if type(geom) == Point or type(geom) == MultiPoint:
                continue
            lines_same.append(geom)
        lines_same = GeometryCollection(lines_same, srid=self.field1.srid)

        # The intersection of the other_geoms will tell us where
        # they're the same.
        other_geom_same = other_geom1.intersection(other_geom2)
        # Always wrap in a GeometryCollection.
        if not type(other_geom_same) == GeometryCollection:
            other_geom_same = GeometryCollection(other_geom_same,
                                                 srid=other_geom_same.srid)

        # Form a collection out of the components of both.
        same = GeometryCollection(
            [g for g in other_geom_same] + [g for g in lines_same],
            srid=lines_same.srid)

        deleted_other_geom = other_geom1.difference(other_geom2)
        deleted_lines = lines1.difference(lines2)
        inserted_other_geom = other_geom2.difference(other_geom1)
        inserted_lines = lines2.difference(lines1)

        deleted = deleted_other_geom.union(deleted_lines)
        inserted = inserted_other_geom.union(inserted_lines)

        return {'same': same, 'deleted': deleted, 'inserted': inserted}

    def as_html(self):
        from django.contrib.gis.geos import Polygon, MultiPolygon
        from olwidget.widgets import InfoMap

        def _convert_to_multipolygon(field):
            l = []
            for p in field:
                if type(p) == MultiPolygon:
                    l += [item for item in p]
                else:
                    l.append(p)
            return MultiPolygon(l, srid=field.srid)

        POLY_TYPES = [Polygon, MultiPolygon]
        olwidget_options = None
        if hasattr(settings, 'OLWIDGET_DEFAULT_OPTIONS'):
            olwidget_options = settings.OLWIDGET_DEFAULT_OPTIONS

        d = self.get_diff()
        if d is None:
            return '<tr><td colspan="2">(No differences found)</td></tr>'

        # Split out polygons from other geometries in field1, field2,
        # same, deleted and inserted.
        poly_field1, misc = self._split_out_geometry(POLY_TYPES, self.field1)
        poly_field2, misc = self._split_out_geometry(POLY_TYPES, self.field2)
        poly_same, other_geom_same = self._split_out_geometry(
            POLY_TYPES, d['same'])
        poly_deleted, other_geom_deleted = self._split_out_geometry(
            POLY_TYPES, d['deleted'])
        poly_inserted, other_geom_inserted = self._split_out_geometry(
            POLY_TYPES, d['inserted'])

        # We need to convert from GeometryCollection to MultiPolygon to
        # compute boundary.
        poly_field1 = _convert_to_multipolygon(poly_field1)
        poly_field2 = _convert_to_multipolygon(poly_field2)

        deleted = InfoMap([
            (poly_same, {'html': 'Stayed the same',
                         'style': {'stroke_opacity': '0'}}),
            (poly_deleted,
                {'html': 'Removed',
                 'style': {'fill_color': '#ff7777', 'stroke_color': '#ff7777',
                           'fill_opacity': '0.8', 'stroke_opacity': '0'}
                }
            ),
            (poly_field1.boundary, {}),
            (other_geom_same,
                {'html': 'Stayed the same',
                 'style': {'fill_color': '#ffdf68', 'stroke_color': '#db9e33',
                           'stroke_opacity': '1'}}
            ),
            (other_geom_deleted,
                {'html': 'Removed',
                 'style': {'fill_color': '#ff7777', 'stroke_color': '#ff7777',
                           'fill_opacity': '0.8', 'stroke_opacity': '1'}
                }
            ),
        ], options=olwidget_options)

        inserted = InfoMap([
            (poly_same, {'html': 'Stayed the same',
                         'style': {'stroke_opacity': '0'}}),
            (poly_inserted,
                {'html': 'Added',
                 'style': {'fill_color': '#9bff53', 'stroke_color': '#9bff53',
                           'fill_opacity': '0.8', 'stroke_opacity': '0'}
                }
            ),
            (poly_field2.boundary, {}),
            (other_geom_same,
                {'html': 'Stayed the same',
                 'style': {'fill_color': '#ffdf68', 'stroke_color': '#db9e33',
                           'stroke_opacity': '1'}}
            ),
            (other_geom_inserted,
                {'html': 'Added',
                 'style': {'fill_color': '#9bff53', 'stroke_color': '#9bff53',
                           'fill_opacity': '0.8', 'stroke_opacity': '1'}
                }
            ),
        ], options=olwidget_options)

        return render_to_string(self.template, {
            'deleted': deleted, 'inserted': inserted
        })

    def _media(self):
        from olwidget.widgets import InfoMap
        return InfoMap([]).media
    media = property(_media)


def get_diff_operations(a, b):
    """
    Returns the minimal diff operations between two strings, using difflib.
    """
    if a == b:
        return None
    operations = []
    sequence_matcher = difflib.SequenceMatcher(None, a, b)
    for opcode in sequence_matcher.get_opcodes():

        operation, start_a, end_a, start_b, end_b = opcode

        deleted = ''.join(a[start_a:end_a])
        inserted = ''.join(b[start_b:end_b])
        if operation == 'equal':
            operations.append({'equal': inserted})
        else:
            operations.append({'deleted': deleted,
                                'inserted': inserted})
    return operations


def get_diff_operations_clean(a, b):
    """
    Returns a cleaned-up, more human-friendly set of diff operations between
    two strings.  Uses diff_match_patch.
    """
    if a == b:
        return None
    dmp = diff_match_patch.diff_match_patch()
    dmp.Diff_Timeout = 0.01
    dmp.Diff_EditCost = 4

    diff = dmp.diff_main(a, b, False)
    dmp.diff_cleanupSemantic(diff)
    op_map = {diff_match_patch.diff_match_patch.DIFF_DELETE: 'deleted',
              diff_match_patch.diff_match_patch.DIFF_EQUAL: 'equal',
              diff_match_patch.diff_match_patch.DIFF_INSERT: 'inserted'
             }
    return [{op_map[op]: data} for op, data in diff]


class Registry(object):
    """
    This tracks what diff util should be used for what model or field.
    """
    def __init__(self):
        self._registry = {}

    def register(self, model_or_field, diff_util):
        """
        Registers a diff util for a particular model or field type.

        For convience, several built-in fields (CharField, TextField,
        FileField, ImageField) are pre-registered.

        NOTE: You generally don't have to register models to use diff() on
              them.  Registering a diff util for a field or model will give
              you the ability to customize behavior or deal with custom
              fields.

        Attrs:
            model_or_field: The type that will be compared (subclass of
                Model or Field)
            diff_util: The class that will do the comparison (subclass
                of BaseModelDiff or BaseFieldDiff, respectively)
        """
        self._registry[model_or_field] = diff_util

    def get_diff_util(self, model_or_field):
        if model_or_field in self._registry:
            return self._registry[model_or_field]
        if model_or_field is models.ForeignKey:
            return BaseModelDiff
        if model_or_field is models.Model:
            return BaseModelDiff
        if model_or_field is models.Field:
            return BaseFieldDiff
        # unregistered, try the base class
        if model_or_field.__base__ is not object:
            # NOTE: I think __base__ will grab the 'right' parent class.
            # This will probably work fine.  The work around
            # (for c in __bases__) is probably too annoying to implement.
            return self.get_diff_util(model_or_field.__base__)

        raise DiffUtilNotFound

registry = Registry()


def register(model_or_field, diff_util):
    """
    Registers a diff util for a particular model or field type.

    For convenience, several built-in fields (CharField, TextField,
    FileField, ImageField) are pre-registered.

    Examples::

        register(models.CharField, TextFieldDiff)
        register(MyModel, MyModelDiff)

    Attrs:
        model_or_field: The type that will be compared (subclass of
            Model or Field)
        diff_util: The class that will do the comparison (subclass
            of BaseModelDiff or BaseFieldDiff, respectively)
    """
    registry.register(model_or_field, diff_util)


def diff(object1, object2):
    """
    Compares two objects (such as model instances) and returns an object that
    can be used to render the differences between the two objects.

    Example::

        >>> m1 = Person(name="Philip")
        >>> m2 = Person(name="Phillip")
        >>> diff(m1, m2).as_dict()
        {'name': [{'equal': 'Phil'}, {'inserted': 'l'}, {'equal': 'ip'}]}
        >>> diff(m1, m2).as_html()
        u('<tr>'
            '<td colspan="2">name</td>'
          '</tr>
          '<tr>'
          '<td colspan="2">'
            '<span class="diff_equal">Phil</span>'
            '<ins>l</ins>'
            '<span class="diff_equal">ip</span>'
          '</td>
          '</tr>')

    This can work with versioned models, e.g.::

        >> diff(m1.history.as_of(version=2),
        >>      m2.history.as_of(version=3)).as_html()
        u('<tr>'
            '<td colspan="2">name</td>'
          '</tr>
          '<tr>'
          '<td colspan="2">'
            '<span class="diff_equal">Phil</span>'
            '<ins>l</ins>'
            '<span class="diff_equal">ip!</span>'
          '</td>
          '</tr>')

    Returns:
        An object that can be used to display differences.  Object will be
        either BaseModelDiff or a subclass.

    Raises:
        DiffUtilNotFound: If there's no registered or inferred diff for
        the objects.
    """
    if is_historical_instance(object1):
        object1 = object1.history_info._object
    if is_historical_instance(object2):
        object2 = object2.history_info._object
    diff_util = registry.get_diff_util(object1.__class__)
    return diff_util(object1, object2)


# Built-in diff utils provided for some of the Django field types.
register(models.CharField, TextFieldDiff)
register(models.TextField, TextFieldDiff)
register(models.FileField, FileFieldDiff)
register(models.ImageField, ImageFieldDiff)

# GeoDjango.
register(gis_models.GeometryField, GeometryFieldDiff)
