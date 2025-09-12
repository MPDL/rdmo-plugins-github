from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .custom_widgets import ExportsChoiceMultiWidget, ExportsSelectMultiple
from .custom_validators import validate_file_path

class ExportsChoiceMultiValueField(forms.MultiValueField):
    widget = ExportsChoiceMultiWidget

    def __init__(self):
        fields = (
            forms.BooleanField(),
            forms.CharField(validators=[validate_file_path]),
        )
        super().__init__(fields)

    def clean(self, value):
        """This method applies to a multi-value field corresponding to 
        a choice in ExportsMultipleChoiceField.
        Every export choice consists of a boolean field and a char field.

        Validate every subvalue in value ([boolean_value, char_value]). 
        Each subvalue is validated against the corresponding Field in self.fields.

        Important: ValidationErrors are NOT raised here, clean() returns
        the choice's errors to the main field ExportsMultipleChoiceField.
        After validating all choices, ExportsMultipleChoiceField 
        raises all ValidationErrors.
        """

        clean_data = []
        errors = []
        if not isinstance(value, list):
            value = self.widget.decompress(value)
        
        for i, field in enumerate(self.fields):
            # i = 0 -> boolean (checkbox for choice selection)
            # i = 1 -> character (text for file path)
            field_value = value[i]

            if field_value in self.empty_values: # self.empty_values = (None, '', [], (), {})
                errors.append(ValidationError(_('A file path is required.'), code='required')) 
                
            try:
                clean_data.append(field.clean(field_value))
            except ValidationError as ee:
                # Collect all validation errors of the subfield in a single list 
                # (ee.error_list: list[ValidationError]). Skip duplicates.
                errors.extend(e for e in ee.error_list if e not in errors)

        out = self.compress(clean_data)
        self.validate(out)
        self.run_validators(out)
        return out, errors

    def compress(self, data_list):
        '''Transform input data_list to a string with the correctly typed value for each subwidget:
            - a boolean value for the checkbox
            - a string value for the text
        '''

        if isinstance(data_list, list) and len(data_list) == 2:
            return ','.join(map(str, data_list))
        
        return 'False,'
    

class ExportsMultipleChoiceField(forms.MultipleChoiceField):
    widget = ExportsSelectMultiple
    choice_field = ExportsChoiceMultiValueField()
    _choice_names = []
    _choices_to_update = {}
    
    @property
    def choice_names(self):
        return self._choice_names
    
    @choice_names.setter
    def choice_names(self, new_names):
        self._choice_names = self.widget.choice_names = new_names

    @property
    def choices_to_update(self):
        return self._choices_to_update

    @choices_to_update.setter
    def choices_to_update(self, new_values):
        self._choices_to_update = self.widget.choices_to_update = new_values

    def to_python(self, value):
        if not value:
            return []
        elif not isinstance(value, (list, tuple)):
            raise ValidationError(
                self.error_messages["invalid_list"], code="invalid_list"
            )
        
        value = [str(multival) for multival in value]

        # value is a list of choices, each choice is a multivalue string with two comma-separated values:
        # i = 0 -> choice key (checkbox value, fix)
        # i = 1 -> file path (input field, modify-able by the user)
        return [f"{multival.split(',')[0]},{multival.split(',')[1].strip()}" for multival in value]

    def clean(self, value):
        '''Validate the given value and return its 'cleaned' value as an
        appropriate Python object.
        
        Validation of this field implies also validation of each of its choices;
        i.e. clean() raises a ValidationError also if any of the choices is not valid.
        In the case of single invalid choices, ValidationError message for this field 
        is an empty string because the error message is displayed below the 
        corresponding choice(s). 
        '''
        
        value = self.to_python(value)

        if value in self.empty_values and self.required: # self.empty_values = (None, '', [], (), {})
            self.widget.errors = {}
            raise ValidationError(_('At least one choice must be selected.'), code='required')

        # validate choice values, which consist of multivalues (boolean and char) 
        for multivalue in value:
            choice_key, text_value = multivalue.split(',')
            if choice_key in self.choice_names:
                out, errors = self.choice_field.clean([True, text_value])
                if len(errors) > 0:
                    self.widget.errors[choice_key] = errors
                else:
                    self.widget.errors.pop(choice_key, None)
        
        # raise ValidationError with empty string after passing errors to corresponding choices
        if len(self.widget.errors) > 0:
            raise ValidationError('')
        
        return value