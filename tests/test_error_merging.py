# -*- coding: utf-8 -*-
"""Tests for merging of schema-level validation errors with field-level errors.

Schema-level validators (``@validates_schema``) that raise
:class:`ValidationError` with a dict of messages keyed by field name have
their errors merged into the same structure as field-level errors, at the
position of the data they point to. Messages that are not tied to a field
are stored under the ``'_schema'`` key. Multiple messages stored under the
same key accumulate instead of overwriting each other.
"""
import pytest

from marshmallow import Schema, fields, validates, validates_schema, ValidationError
from marshmallow.utils import merge_errors


class TestMergeErrorsUtil:

    def test_merge_lists(self):
        assert merge_errors(['a'], ['b', 'c']) == ['a', 'b', 'c']

    def test_merge_dicts_concatenates_messages_for_same_key(self):
        errors = merge_errors({'foo': ['a']}, {'foo': ['b'], 'bar': ['c']})
        assert errors == {'foo': ['a', 'b'], 'bar': ['c']}

    def test_merge_dicts_recursively(self):
        errors1 = {'nested': {'foo': ['a']}}
        errors2 = {'nested': {'bar': ['b']}}
        assert merge_errors(errors1, errors2) == {'nested': {'foo': ['a'], 'bar': ['b']}}

    def test_merge_list_into_dict_goes_to_schema_key(self):
        assert merge_errors({'foo': ['a']}, ['b']) == {'foo': ['a'], '_schema': ['b']}
        assert merge_errors(['b'], {'foo': ['a']}) == {'foo': ['a'], '_schema': ['b']}

    def test_merge_single_messages(self):
        assert merge_errors('a', 'b') == ['a', 'b']
        assert merge_errors('a', ['b']) == ['a', 'b']
        assert merge_errors(['a'], 'b') == ['a', 'b']

    def test_merge_with_empty(self):
        assert merge_errors({}, {'foo': ['a']}) == {'foo': ['a']}
        assert merge_errors({'foo': ['a']}, {}) == {'foo': ['a']}
        assert merge_errors(None, ['a']) == ['a']
        assert merge_errors(['a'], None) == ['a']


class TestSchemaLevelErrorMerging:

    def test_dict_messages_merge_onto_fields_and_nothing_is_lost(self):
        # Minimal reproduction: three schema validators, one raises a dict
        # keyed by 'name', another raises a different dict keyed by 'name',
        # the third raises a plain string.
        class MySchema(Schema):
            name = fields.Str()
            age = fields.Int()

            @validates_schema(skip_on_field_errors=False)
            def rule_a(self, data):
                raise ValidationError({'name': ['name error A']})

            @validates_schema(skip_on_field_errors=False)
            def rule_b(self, data):
                raise ValidationError({'name': ['name error B']})

            @validates_schema(skip_on_field_errors=False)
            def rule_c(self, data):
                raise ValidationError('schema-only error')

        with pytest.raises(ValidationError) as excinfo:
            MySchema().load({'name': 'x', 'age': 1})
        assert excinfo.value.messages == {
            'name': ['name error A', 'name error B'],
            '_schema': ['schema-only error'],
        }

    def test_schema_errors_merge_with_field_type_errors(self):
        class MySchema(Schema):
            name = fields.Str()
            age = fields.Int()

            @validates_schema(skip_on_field_errors=False)
            def rule(self, data):
                raise ValidationError({'age': ['age rule failed']})

        with pytest.raises(ValidationError) as excinfo:
            MySchema().load({'name': 'x', 'age': 'not an int'})
        # The field's own type error and the schema rule's error live in
        # the same place, type error first
        assert excinfo.value.messages == {
            'age': ['Not a valid integer.', 'age rule failed'],
        }

    def test_schema_errors_merge_with_validates_decorator_errors(self):
        class MySchema(Schema):
            age = fields.Int()

            @validates('age')
            def validate_age(self, value):
                raise ValidationError('from field validator')

            @validates_schema(skip_on_field_errors=False)
            def rule(self, data):
                raise ValidationError({'age': ['from schema validator']})

        with pytest.raises(ValidationError) as excinfo:
            MySchema().load({'age': 5})
        assert excinfo.value.messages == {
            'age': ['from field validator', 'from schema validator'],
        }

    def test_message_order_is_preserved(self):
        class MySchema(Schema):
            name = fields.Str()

            @validates_schema(skip_on_field_errors=False)
            def rule_1(self, data):
                raise ValidationError({'name': ['first']})

            @validates_schema(skip_on_field_errors=False)
            def rule_2(self, data):
                raise ValidationError({'name': ['second']})

            @validates_schema(skip_on_field_errors=False)
            def rule_3(self, data):
                raise ValidationError({'name': ['third']})

        errors = MySchema().validate({'name': 'x'})
        assert errors['name'] == ['first', 'second', 'third']

    def test_error_raised_with_field_name_lands_on_that_field(self):
        # ValidationError(message, field_name): the second positional
        # argument is a single field name
        class MySchema(Schema):
            name = fields.Str()

            @validates_schema(skip_on_field_errors=False)
            def rule(self, data):
                raise ValidationError('positional field error', 'name')

        errors = MySchema().validate({'name': 'x'})
        assert errors == {'name': ['positional field error']}

    def test_non_field_messages_stay_under_schema_key(self):
        class MySchema(Schema):
            name = fields.Str()

            @validates_schema(skip_on_field_errors=False)
            def rule_a(self, data):
                raise ValidationError('first schema error')

            @validates_schema(skip_on_field_errors=False)
            def rule_b(self, data):
                raise ValidationError('second schema error')

        errors = MySchema().validate({'name': 'x'})
        assert errors == {'_schema': ['first schema error', 'second schema error']}

    def test_nested_schema_errors_merge_by_data_path(self):
        class AddressSchema(Schema):
            street = fields.Str()
            city = fields.Str()

            @validates_schema(skip_on_field_errors=False)
            def check_street(self, data):
                raise ValidationError({'street': ['street rule failed']})

        class OrderSchema(Schema):
            address = fields.Nested(AddressSchema)

            @validates_schema(skip_on_field_errors=False)
            def check_city(self, data):
                raise ValidationError({'address': {'city': ['city rule failed']}})

        errors = OrderSchema().validate({'address': {'street': 's', 'city': 'c'}})
        # Errors from both levels hang off the same nested structure,
        # mirroring the shape of the input data
        assert errors == {
            'address': {
                'street': ['street rule failed'],
                'city': ['city rule failed'],
            },
        }

    def test_nested_field_errors_merge_with_schema_errors(self):
        class AddressSchema(Schema):
            street = fields.Str(required=True)

        class OrderSchema(Schema):
            address = fields.Nested(AddressSchema)

            @validates_schema(skip_on_field_errors=False)
            def check_address(self, data):
                raise ValidationError({'address': {'street': ['street rule failed']}})

        errors = OrderSchema().validate({'address': {}})
        assert errors == {
            'address': {
                'street': ['Missing data for required field.', 'street rule failed'],
            },
        }

    def test_errors_merge_per_index_with_many(self):
        class MySchema(Schema):
            age = fields.Int()

            @validates_schema(skip_on_field_errors=False)
            def rule(self, data):
                raise ValidationError({'age': ['age rule failed']})

            @validates_schema(skip_on_field_errors=False)
            def rule2(self, data):
                raise ValidationError('whole-item error')

        errors = MySchema().validate([{'age': 'bad'}, {'age': 1}], many=True)
        assert errors == {
            0: {
                'age': ['Not a valid integer.', 'age rule failed'],
                '_schema': ['whole-item error'],
            },
            1: {
                'age': ['age rule failed'],
                '_schema': ['whole-item error'],
            },
        }

    def test_load_validate_and_handle_error_report_same_messages(self):
        captured = {}

        class MySchema(Schema):
            name = fields.Str()
            age = fields.Int()

            @validates_schema(skip_on_field_errors=False)
            def rule(self, data):
                raise ValidationError({'name': ['name rule failed']})

            def handle_error(self, error, data):
                captured['messages'] = error.messages

        data = {'name': 'x', 'age': 'not an int'}
        with pytest.raises(ValidationError) as excinfo:
            MySchema().load(data)
        load_messages = excinfo.value.messages
        validate_messages = MySchema().validate(data)
        assert load_messages == validate_messages == captured['messages']
        assert load_messages == {
            'age': ['Not a valid integer.'],
            'name': ['name rule failed'],
        }
