"""
Attributes/methods of historical instances.

Because we are constructing our historical classes dynamically
we can't simply write down "class MyHistoricalInstance(..)..."
so we have the methods laid out flat here.
"""
import datetime

from django.db import models
from django.db.models import Max

from fields import *
from constants import *
from utils import *

def get_history_fields(self, model):
    """
    Returns a dictionary of the essential fields that will be added to the
    historical record model, in addition to the ones returned by models.copy_fields.
    """
    fields = {
        'history_id': models.AutoField(primary_key=True),
        'history__object': HistoricalObjectDescriptor(model),
        'history_date': models.DateTimeField(default=datetime.datetime.now),
        'history_version_number': version_number_of,
        'history_type': models.SmallIntegerField(choices=TYPE_CHOICES),
        # If you want to display "Reverted to version N" in every change
        # comment then you should stash that in the comment field
        # directly rather than using
        # reverted_to_version.version_number() on each display.
        'history_reverted_to_version': models.ForeignKey('self', null=True),
        # lookup function for cleaniness. Instead of doing
        # h.history_ip_address we can write h.history_info.ip_address
        'history_info': HistoricalMetaInfo(),
        'revert_to': revert_to,
        'save': save_with_arguments,
        'delete': delete_with_arguments,
        '__init__': historical_record_init,
        #'__getattribute__':
        #    # not sure why functools.partial doesn't work here
        #    lambda m, name: historical_record_getattribute(model, m, name),
    }

    return fields

def historical_record_init(m, *args, **kws):
    """
    This is the __init__ method of historical record instances.
    """
    retval = super(m.__class__, m).__init__(*args, **kws)
    _wrap_foreign_keys(m)
    _wrap_reverse_lookups(m)
    return retval

def _wrap_foreign_keys(m):
    """
    Sets the foreign key fields to the historical versions of the
    foreign keys if the related objects are versioned.

    Wraps these in SimpleLazyObjects so the lookup doesn't happen unless
    the objects are used.

    @param m: a historical record model
    """
    #def _lookup_version(m, fk_obj):
    #    history_manager = getattr(fk_obj, fk_obj._history_manager_name)
    #    print "looking up as of..", m.history_info.date
    #    return history_manager.as_of(date=m.history_info.date)

#    def _wrap_field(m, field):
#        print "..ONONON:", m.__class__, "ENDON"
#        print "fk obj getting:", field.name, "ENDGETPRINT"
#        fk_obj = getattr(m, field.name)
#        if is_versioned(fk_obj):
#            print "HAS ATTR OF HISTORY MANAGER"
#            _lookup_proper_fk_version = partial(_lookup_version, m, fk_obj)
#            print "SET", field.name, "to lookup old fk version on", m
#            #m._direct_lookup_fields[field.name] = SimpleLazyObject(
#            #    _lookup_proper_fk_version
#            #)
#
    #m._direct_lookup_fields = {}
    #for field in m.history_info._object._meta.fields:
    #    if isinstance(field, models.ForeignKey):
    #        _wrap_field(m, field)
    #for field in m._meta.many_to_many:
    #    _wrap_field(m, field)
    for field in m._meta.fields:
        if isinstance(field, VersionedForeignKey):
            setattr(m, field._attribute_name, field.lookup_proper_version(m))
    #XXX
    # AGhhh i think we need to use our custom __getattribute__, god
    # damnit
    for field in m._meta.many_to_many:
        if is_versioned(field.related.parent_model):
            setattr()

def _wrap_reverse_lookups(m):
    """
    Make reverse foreign key lookups return historical versions
    if the parent model is versioned.

    @param m: a historical record model
    """
    def _reverse_set_lookup(m, rel_o):
        attr = rel_o.field.attname
        parent_model = rel_o.model
        as_of = m.history_info.date
        parent_pk_att = parent_model._meta.pk.attname

        # We want to make sure our filter respects the fact that the
        # pk of the model can cycle while the unique fields stay the
        # same.

        # Grab parent history objects that are less than the as_of date
        qs = parent_model.history.filter(history_date__lte=as_of, **{attr:m.id})
        # Then group by the parent_pk
        qs = qs.order_by(parent_pk_att).values(parent_pk_att).distinct()
        # then annotate the maximum history object id
        #ids = qs.values('history_id').annotate(Max('history_id'))
        ids = qs.annotate(Max('history_id'))
        history_ids = [ v['history_id__max'] for v in ids ]

        # return a QuerySet containing the proper history objects
        return parent_model.history.filter(history_id__in=history_ids)

    def _reverse_attr_lookup(m, rel_o):
        parent_model = rel_o.model
        # attname looks like 'b_id' here because this is a OneToOneField
        attr = rel_o.field.attname
        as_of = m.history_info.date

        return parent_model.history.filter(
            history_date__lte=as_of,
            # we filter on something that looks like 'b_id' because we
            # are using a VersionedForeignKey field on the parent model
            **{attr:m.id}
        )[0]

    related_objects = m.history_info._object._meta.get_all_related_objects()
    related_versioned = [ o for o in related_objects if is_versioned(o.model) ]
    for rel_o in related_versioned:
        # set the accessor to a lazy lookup function that, when
        # accessed, looks up the proper related set
        accessor = rel_o.get_accessor_name()
        
        if isinstance(rel_o.field, models.OneToOneField):
            # OneToOneFields have a direct lookup (not a set)
            _proper_reverse_lookup = partial(_reverse_attr_lookup, m, rel_o)
        else:
            _proper_reverse_lookup = partial(_reverse_set_lookup, m, rel_o)
        setattr(m, accessor, SimpleLazyObject(_proper_reverse_lookup))

#def historical_record_getattribute(model, m, name):
#    """
#    We have to define our own __getattribute__ because otherwise there's
#    no way to set our wrapped foreign key attributes.
#    
#    If we try and set
#    history_instance.att = SimpleLazyObject(_lookup_proper_fk_version)
#    we'll get an error because Django does an isinstance() check on
#    assignment to model fields.  Additionally, the __set__ method on
#    related fields will force evaluation due to equality checks.
#    """
#    basedict = model.__getattribute__(m, '__dict__')
#    direct_val = basedict.get('_direct_lookup_fields', {}).get(name)
#    if direct_val is not None:
#        return direct_val
#    print "BASEDICT", basedict
#    print "name:", name
#    print "  result:", model.__getattribute__(m, name)
#    return model.__getattribute__(m, name)

def revert_to(hm, delete_newer_versions=False, **kws):
    """
    This is used on a *historical instance* - e.g. something you get
    using history.get(..) rather than an instance of the model.  Like:

    ph = p.history.as_of(..)
    ph.revert_to()

    Reverts to this version of the model.

    @param delete_newer_versions: if True, delete all versions in the
             history newer than this version.
    @param track_changes: if False, won't log *this revert* as an action in
             the history log.
    """
    # maybe-TODO At some point we may want to pull this out into some
    # kind of hm.history_info.get_instance() method. Providing
    # get_instance() would be a liability, though, because we want to
    # encourage interaction with the historical instance -- it does
    # fancy foreignkey lookups, etc. 
    m = hm.history_info._object

    # If we simply grab hm.history_info._object we may hit a uniqueness exception
    # if we save the model and it already exists.  This is because the pk of the
    # model may have changed if it was deleted at some time.

    # Get the current model instance if it exists and set the pk
    # accordingly.
    unique_fields = unique_fields_of(m)
    if unique_fields:
        ms = m.__class__.objects.filter(**unique_fields)
        if ms:
            # keep the primary key unique
            m.pk = ms[0].pk

    if delete_newer_versions:
        newer = m.history.filter(history_info__date__gt=hm.history_info.date)
        for v in newer:
            v.delete(track_changes=False)

    m._history_type = TYPE_REVERTED

    if hm.history_info.type == TYPE_DELETED:
        # We are reverting to a deleted version of the model
        # so..delete the model!
        m.delete(reverted_to_version=hm)
    else:
        m.save(reverted_to_version=hm, **kws)

def version_number_of(hm):
    """
    Returns version number.

    @param hm: history object.
    """
    if getattr(hm.history_info.instance, '_version_number', None) is None:
        date = hm.history_info.date
        obj = hm.history_info._object
        hm.history_info.instance._version_number = len(
            obj.history.filter(history_date__lte=date)
        )
    return hm.history_info.instance._version_number

class HistoricalMetaInfo(object):
    def __get__(self, instance, owner):
        self.instance = instance
        return self

    def __getattr__(self, name):
        try:
            return getattr(self.__dict__['instance'], 'history_%s' % name)
        except AttributeError, s:
            raise AttributeError("history_info has no attribute %s" % name)

    def __setattr__(self, name, val):
        if name == 'instance':
            self.__dict__['instance'] = val
            return
        try:
            self.__dict__['instance'].__setattr__('history_%s' % name, val)
        except AttributeError, s:
            raise AttributeError("history_info has no attribute %s" % name)


class HistoricalObjectDescriptor(object):
    def __init__(self, model):
        self.model = model

    def __get__(self, instance, owner):
        values = (getattr(instance, f.attname) for f in self.model._meta.fields)
        m = self.model(*values)
        return m