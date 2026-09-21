"""Browser form contracts."""

import re
from datetime import date

from flask_wtf import FlaskForm
from wtforms import BooleanField, DateField, PasswordField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length, ValidationError

from flaskapp.countries import COUNTRY_CHOICES


class LoginForm(FlaskForm):
    email = StringField("Email address", validators=[DataRequired(), Email(), Length(max=254)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8, max=128)])
    remember = BooleanField("Keep me signed in")
    submit = SubmitField("Sign in")


def strong_password(_form, field) -> None:
    """Require a practical baseline without silently modifying the password."""
    value = field.data or ""
    checks = (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]")
    if len(value) < 12 or not all(re.search(pattern, value) for pattern in checks):
        raise ValidationError(
            "Use at least 12 characters with uppercase, lowercase, number, and special character."
        )


def valid_birthday(_form, field) -> None:
    if field.data and field.data >= date.today():
        raise ValidationError("Birthday must be a date in the past.")


class RegistrationForm(FlaskForm):
    name = StringField("Full name", validators=[DataRequired(), Length(min=2, max=100)])
    email = StringField("Email address", validators=[DataRequired(), Email(), Length(max=254)])
    country = SelectField("Country", choices=COUNTRY_CHOICES, validators=[DataRequired()])
    birthday = DateField("Birthday", validators=[DataRequired(), valid_birthday])
    password = PasswordField(
        "Password", validators=[DataRequired(), Length(max=128), strong_password]
    )
    confirm_password = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
    submit = SubmitField("Create account")


class ResetPasswordForm(FlaskForm):
    email = StringField("Email address", validators=[DataRequired(), Email(), Length(max=254)])
    password = PasswordField(
        "New password", validators=[DataRequired(), Length(max=128), strong_password]
    )
    confirm_password = PasswordField(
        "Confirm new password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
    submit = SubmitField("Reset password")
