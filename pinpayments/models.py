"""
Models for interacting with Pin, and storing results
"""
from __future__ import unicode_literals

from datetime import datetime
from decimal import Decimal
from pinpayments import logger
import warnings

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.encoding import python_2_unicode_compatible
from django.utils.timezone import get_default_timezone
from django.utils.translation import ugettext_lazy as _

from pinpayments.exceptions import ConfigError, PinError
from pinpayments.managers import CardTokenManager, CustomerTokenManager
from pinpayments.objects import PinEnvironment
from pinpayments.utils import get_value


if getattr(settings, 'PIN_ENVIRONMENTS', {}) == {}:
    raise ConfigError("PIN_ENVIRONMENTS not defined.")

property_deprecation_warning_message = \
    """This property accessor method will be removed in a future version. """ \
    """Please review the django-pinpayments v1.1.0 changes."""

TRANS_TYPE_CHOICES = (
    ('Payment', 'Payment'),
    ('Refund', 'Refund'),
)

CARD_TYPES = (
    ('master', 'Mastercard'),
    ('visa', 'Visa'),
)


@python_2_unicode_compatible
class CardTokenAbstract(models.Model):
    """
        An abstract CardToken model for use with https://pin.net.au/docs/api/cards API calls.
    """
    created = models.DateTimeField(_('Created'), auto_now_add=True)
    environment = models.CharField(
        max_length=25, db_index=True, blank=True,
        help_text=_('The name of the Pin environment to use, eg test or live.')
    )

    token = models.CharField(
        _('Token'), max_length=100,
        help_text=_('Generated by Card API or Customers API')
    )
    scheme = models.CharField(
        _('Card type'), max_length=20, blank=True, null=True,
        choices=CARD_TYPES, help_text=_('Determined automatically by Pin')
    )
    name = models.CharField(
        _('Cardholder name'), max_length=100, blank=True, null=True,
        help_text=_('Cleansed by Pin API')
    )
    display_number = models.CharField(
        _('Card display number'), max_length=100, blank=True, null=True,
        help_text=_('Cleansed by Pin API')
    )
    expiry_month = models.IntegerField(
        _('Card expiry month'), blank=True, null=True, default=None,
    )
    expiry_year = models.IntegerField(
        _('Card expiry year'), blank=True, null=True, default=None,
    )

    address_line1 = models.CharField(
        _('Cardholder street address'), max_length=100, blank=True, null=True,
        help_text=_('Address entered by customer to process this transaction')
    )
    address_line2 = models.CharField(
        _('Cardholder street address Line 2'), max_length=100, blank=True,
        null=True
    )
    address_city = models.CharField(
        _('Cardholder city'), max_length=100, blank=True, null=True
    )
    address_state = models.CharField(
        _('Cardholder state'), max_length=100, blank=True, null=True
    )
    address_postcode = models.CharField(
        _('Cardholder postal / ZIP code'), max_length=100, blank=True,
        null=True
    )
    address_country = models.CharField(
        _('Cardholder country'), max_length=100, blank=True, null=True
    )

    primary = models.NullBooleanField(_("Customer's primary card"), default=False)

    objects = CardTokenManager()

    class Meta:
        abstract = True
        ordering = ['created']

    def __str__(self):
        return "{0}".format(self.token)

    @property
    def expiry_str(self):
        if None in (self.expiry_month, self.expiry_year):
            return ""
        return "{0}/{1}".format(str(self.expiry_month).zfill(2), self.expiry_year)

    @property
    def has_expired(self):
        today = timezone.now().date()

        # constructed dates below are the first day upon which the credit card has expired.
        if self.expiry_month < 12:
            # Months 1-11
            return today >= datetime.date(self.expiry_year, self.expiry_month + 1, 1)
        # Month 12
        return today >= datetime.date(self.expiry_year + 1, 1, 1)


class CardToken(CardTokenAbstract):
    """
        Implements the CardToken model as non abstract.
    """
    pass


@python_2_unicode_compatible
class CustomerTokenAbstract(models.Model):
    """
    A token returned by the Pin Payments Customer API.

    These can be used on a Transaction in lieu of of a Card token, and
    can be reused.

    You can use this class to implement you own CustomToken to model type other than
    the user auth model, or you can use the CustomerToken implementation below this
    class which is already FK-ed to the User auth model specified in your settings.py
    """
    environment = models.CharField(
        max_length=25, db_index=True, blank=True,
        help_text=_('The name of the Pin environment to use, eg test or live.')
    )
    token = models.CharField(
        _('Token'), max_length=100,
        help_text=_('Generated by Card API or Customers API')
    )
    created = models.DateTimeField(_('Created'), auto_now_add=True)
    active = models.BooleanField(_('Active'), default=True)

    cards = models.ManyToManyField(CardToken, blank=True)

    objects = CustomerTokenManager()

    class Meta:
        abstract = True

    def __str__(self):
        return "{0}".format(self.token)

    @property
    def primary_card(self):
        try:
            return self.cards.get(primary=True)
        except CardToken.MultipleObjectsReturned:
            logger.warning("CustomerToken: {0} has more than one CardToken ".format(self.token) +
                           "with primary=True, you should synchronize this customer's card tokens.")
            return self.cards.filter(primary=True)[:1][0]  # equivalent to .first() but supports Django 1.5
        except CardToken.DoesNotExist:
            pass
        return None

    @property
    def first_card(self):
        """
            Cards should only be accessed via .primary_card or .cards queryset. This is a legacy/backwards
            compatibility accessor.
        """
        # TODO: Remove when dropping Django 1.6 support
        warnings.warn(property_deprecation_warning_message, DeprecationWarning)
        return self.cards.filter(primary=True)[:1][0]  # equivalent to .first() but supports Django 1.5

    @property
    def card_type(self):
        # TODO: Remove when dropping Django 1.6 support
        warnings.warn(property_deprecation_warning_message, DeprecationWarning)
        return self.first_card.scheme

    @property
    def card_number(self):
        # TODO: Remove when dropping Django 1.6 support
        warnings.warn(property_deprecation_warning_message, DeprecationWarning)
        return self.first_card.display_number

    @property
    def card_name(self):
        # TODO: Remove when dropping Django 1.6 support
        warnings.warn(property_deprecation_warning_message, DeprecationWarning)
        return self.first_card.name

    def save(self, *args, **kwargs):
        if not self.environment:
            self.environment = getattr(settings, 'PIN_DEFAULT_ENVIRONMENT', 'test')
        super(CustomerTokenAbstract, self).save(*args, **kwargs)

    def new_card_token(self, card_token):
        """ Placeholder to retain name and functionality of old method """
        self.update_card(card_token)
        return True

    def update_card(self, card_token):
        """ Provide a card token to update the details for this customer """
        pin_env = PinEnvironment(self.environment)
        payload = {'card_token': card_token}
        url_tail = "/customers/{1}".format(self.token)
        data = pin_env.pin_put(url_tail, payload)[1]['response']
        self.card_number = data['card']['display_number']
        self.card_type = data['card']['scheme']
        self.card_name = data['card']['name']
        self.save()

    def add_card_token(self, card_token):
        return self._meta.default_manager.add_card_token_to_customer(self, card_token)

    def delete_card(self, card):
        return self._meta.default_manager.delete_card_from_customer(self, card)

    def set_primary_card(self, card):
        return self._meta.default_manager.set_primary_card_for_customer(self, card)

    @classmethod
    def create_from_card_token(cls, card_token, user, environment=''):
        # TODO: Remove when dropping Django 1.6 support
        warnings.warn(
            "The classmethod CustomerToken.create_from_card_token() will be "
            "removed in the future, use the model's manager method "
            "CustomerToken.objects.create_from_card_token() instead",
            DeprecationWarning)
        if not environment:
            environment = None
        return cls.objects.create_from_card_token(card_token, user, environment)


class CustomerToken(CustomerTokenAbstract):
    """
    This is a model implementation of the CustomerTokenAbstract class above.
    It FKs to the User auth model by default

    This model is typically used for recurring billing and managing multiple
    credit cards attached to a User's account
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL)


@python_2_unicode_compatible
class PinTransaction(models.Model):
    """
    PinTransaction - model to hold response data from the pin.net.au
    Charge API. Note we capture the card and/or customer token, but
    there's no FK to your own customers table. That's for you to do
    in your own code.
    """
    date = models.DateTimeField(
        _('Date'), db_index=True, help_text=_(
            'Time this transaction was put in the database. '
            'May differ from the time that PIN reports the transaction.'
        )
    )
    environment = models.CharField(
        max_length=25, db_index=True, blank=True,
        help_text=_('The name of the Pin environment to use, eg test or live.')
    )
    amount = models.DecimalField(
        _('Amount (Dollars)'), max_digits=10, decimal_places=2
    )
    fees = models.DecimalField(
        _('Transaction Fees'), max_digits=10, decimal_places=2,
        default=Decimal("0.00"), blank=True, null=True, help_text=_(
            'Fees charged to you by Pin, for this transaction, in dollars'
        )
    )
    description = models.TextField(
        _('Description'), blank=True, null=True,
        help_text=_('As provided when you initiated the transaction')
    )
    processed = models.BooleanField(
        _('Processed?'), default=False,
        help_text=_('Has this been sent to Pin yet?')
    )
    succeeded = models.BooleanField(
        _('Success?'), default=False,
        help_text=_('Was the transaction approved?')
    )
    currency = models.CharField(
        _('Currency'), max_length=100, default='AUD',
        help_text=_('Currency transaction was processed in')
    )
    transaction_token = models.CharField(
        _('Pin API Transaction Token'), max_length=100, blank=True, null=True,
        db_index=True, help_text=_('Unique ID from Pin for this transaction')
    )
    card_token = models.CharField(
        _('Pin API Card Token'), max_length=40, blank=True, null=True,
        help_text=_(
            'Card token used for this transaction (Card API and Web Forms)'
        )
    )
    customer_token = models.ForeignKey(
        CustomerToken, blank=True, null=True,
        help_text=_('Provided by Customer API')
    )
    pin_response = models.CharField(
        _('API Response'), max_length=255, blank=True, null=True,
        help_text=_('Response text, usually Success!')
    )
    ip_address = models.GenericIPAddressField(
        help_text=_('IP Address used for payment')
    )
    email_address = models.EmailField(
        _('E-Mail Address'), max_length=100, help_text=_('As passed to Pin.')
    )
    card_address1 = models.CharField(
        _('Cardholder Street Address'), max_length=100, blank=True, null=True,
        help_text=_('Address entered by customer to process this transaction')
    )
    card_address2 = models.CharField(
        _('Cardholder Street Address Line 2'), max_length=100, blank=True,
        null=True
    )
    card_city = models.CharField(
        _('Cardholder City'), max_length=100, blank=True, null=True
    )
    card_state = models.CharField(
        _('Cardholder State'), max_length=100, blank=True, null=True
    )
    card_postcode = models.CharField(
        _('Cardholder Postal / ZIP Code'), max_length=100, blank=True,
        null=True
    )
    card_country = models.CharField(
        _('Cardholder Country'), max_length=100, blank=True, null=True
    )
    card_number = models.CharField(
        _('Card Number'), max_length=100, blank=True, null=True,
        help_text=_('Cleansed by Pin API')
    )
    card_type = models.CharField(
        _('Card Type'), max_length=20, blank=True, null=True,
        choices=CARD_TYPES, help_text=_('Determined automatically by Pin')
    )
    pin_response_text = models.TextField(
        _('Complete API Response'), blank=True, null=True,
        help_text=_('The full JSON response from the Pin API')
    )

    def save(self, *args, **kwargs):
        if not (self.card_token or self.customer_token):
            raise PinError("Must provide card_token or customer_token")

        if self.card_token and self.customer_token:
            raise PinError("Can only provide card_token OR customer_token, not both")

        if not self.environment:
            self.environment = getattr(settings, 'PIN_DEFAULT_ENVIRONMENT', 'test')

        if self.environment not in getattr(settings, 'PIN_ENVIRONMENTS', {}):
            raise PinError("Pin Environment '{0}' does not exist".format(self.environment))

        if not self.date:
            now = datetime.now()
            if settings.USE_TZ:
                now = timezone.make_aware(now, get_default_timezone())
            self.date = now

        super(PinTransaction, self).save(*args, **kwargs)

    def __str__(self):
        return "{0}".format(self.id)

    class Meta:
        verbose_name = 'PIN.net.au Transaction'
        verbose_name_plural = 'PIN.net.au Transactions'
        ordering = ['-date']

    def process_transaction(self):
        """ Send the data to Pin for processing """
        if self.processed:
            return None  # can only attempt to process once.
        self.processed = True
        self.save()

        pin_env = PinEnvironment(self.environment)
        payload = {
            'email': self.email_address,
            'description': self.description,
            'amount': int(self.amount * 100),
            'currency': self.currency,
            'ip_address': self.ip_address,
        }
        if self.card_token:
            payload['card_token'] = self.card_token
        else:
            payload['customer_token'] = self.customer_token.token

        response, response_json = pin_env.pin_post('/charges', payload, True)
        self.pin_response_text = response.text

        if response_json is None:
            self.pin_response = 'Failure.'
        elif 'error' in response_json.keys():
            if 'messages' in response_json.keys():
                if 'message' in response_json['messages'][0].keys():
                    self.pin_response = 'Failure: {0}'.format(
                        response_json['messages'][0]['message']
                    )
            else:
                self.pin_response = 'Failure: {0}'.format(
                    response_json['error_description']
                )
            self.transaction_token = response_json.get('charge_token', None)
        else:
            data = response_json['response']
            self.succeeded = True
            self.transaction_token = data['token']
            self.fees = data['total_fees'] / Decimal("100.00")
            self.pin_response = data['status_message']
            self.card_address1 = data['card']['address_line1']
            self.card_address2 = data['card']['address_line2']
            self.card_city = data['card']['address_city']
            self.card_state = data['card']['address_state']
            self.card_postcode = data['card']['address_postcode']
            self.card_country = data['card']['address_country']
            self.card_number = data['card']['display_number']
            self.card_type = data['card']['scheme']

        self.save()
        return self.pin_response


@python_2_unicode_compatible
class BankAccount(models.Model):
    """ A representation of a bank account, as stored by Pin. """
    token = models.CharField(
        _('Pin API Bank account token'), max_length=40, db_index=True,
        help_text=_("A bank account token provided by Pin")
    )
    bank_name = models.CharField(
        _('Bank Name'), max_length=100,
        help_text=_("The name of the bank at which this account is held")
    )
    branch = models.CharField(
        _('Branch name'), max_length=100, blank=True,
        help_text=_("The name of the branch at which this account is held")
    )
    name = models.CharField(
        _('Recipient Name'), max_length=100,
        help_text="The name of the bank account"
    )
    bsb = models.IntegerField(
        _('BSB'),
        help_text=_("The BSB (Bank State Branch) code of the bank account.")
    )
    number = models.CharField(
        _('BSB'), max_length=20,
        help_text=_("The account number of the bank account")
    )
    environment = models.CharField(
        max_length=25, db_index=True, blank=True,
        help_text=_('The name of the Pin environment to use, eg test or live.')
    )

    def __str__(self):
        return "{0}".format(self.token)


@python_2_unicode_compatible
class PinRecipient(models.Model):
    """
    A recipient stored for the purpose of having funds transferred to them
    """
    token = models.CharField(
        max_length=40, db_index=True,
        help_text=_("A recipient token provided by Pin")
    )
    email = models.EmailField(max_length=100, help_text=_('As passed to Pin.'))
    name = models.CharField(
        max_length=100, blank=True, null=True,
        help_text=_("Optional. The name by which the recipient is referenced")
    )
    created = models.DateTimeField(_("Time created"), auto_now_add=True)
    bank_account = models.ForeignKey(
        BankAccount, blank=True, null=True
    )
    environment = models.CharField(
        max_length=25, db_index=True, blank=True,
        help_text=_('The name of the Pin environment to use, eg test or live.')
    )

    def __str__(self):
        return "{0}".format(self.token)

    @classmethod
    def create_with_bank_account(cls, email, account_name, bsb, number, name=""):
        """ Creates a new recipient from a provided bank account's details """
        pin_env = PinEnvironment()
        payload = {
            'email': email,
            'name': name,
            'bank_account[name]': account_name,
            'bank_account[bsb]': bsb,
            'bank_account[number]': number
        }
        data = pin_env.pin_post('/recipients', payload)[1]['response']
        bank_account = BankAccount.objects.create(
            bank_name=data['bank_account']['bank_name'],
            branch=data['bank_account']['branch'],
            bsb=data['bank_account']['bsb'],
            name=data['bank_account']['name'],
            number=data['bank_account']['number'],
            token=data['bank_account']['token'],
            environment=pin_env.name,
        )
        new_recipient = cls.objects.create(
            token=data['token'],
            email=data['email'],
            name=data['name'],
            bank_account=bank_account,
            environment=pin_env.name,
        )
        return new_recipient


@python_2_unicode_compatible
class PinTransfer(models.Model):
    """
    A transfer from a PinEnvironment to a PinRecipient
    """
    transfer_token = models.CharField(
        _('Pin API Transfer Token'), max_length=100, blank=True, null=True,
        db_index=True, help_text=_('Unique ID from Pin for this transfer')
    )
    status = models.CharField(
        max_length=100, blank=True, null=True,
        help_text=_("Status of transfer at time of saving")
    )
    currency = models.CharField(
        max_length=10, help_text=_("currency of transfer")
    )
    description = models.CharField(
        max_length=100, blank=True, null=True,
        help_text=_("Description as shown on statement")
    )
    amount = models.IntegerField(help_text=_(
        "Transfer amount, in the base unit of the "
        "currency (e.g.: cents for AUD, yen for JPY)"
    ))
    recipient = models.ForeignKey(PinRecipient, blank=True, null=True)
    created = models.DateTimeField(auto_now_add=True)
    pin_response_text = models.TextField(
        _('Complete API Response'), blank=True, null=True,
        help_text=_('The full JSON response from the Pin API')
    )

    def __str__(self):
        return "{0}".format(self.transfer_token)

    @property
    def value(self):
        """
        Returns the value of the transfer in the representation of the
        currency it is in, without symbols
        That is, 1000 cents as 10.00, 1000 yen as 1000
        """
        return get_value(self.amount, self.currency)

    @classmethod
    def send_new(cls, amount, description, recipient, currency="AUD"):
        """ Creates a transfer by sending it to Pin """
        pin_env = PinEnvironment()
        payload = {
            'amount': amount,
            'description': description,
            'recipient': recipient.token,
            'currency': currency,
        }
        response, response_json = pin_env.pin_post('/transfers', payload)
        data = response_json['response']
        new_transfer = PinTransfer.objects.create(
            transfer_token=data['token'],
            status=data['status'],
            currency=data['currency'],
            description=data['description'],
            amount=data['amount'],
            recipient=recipient,
            pin_response_text=response.text,
        )
        return new_transfer
