from django.db import models
from django.conf import settings
from django.contrib.auth.models import User
from django.utils.translation import ugettext_lazy as _

import requests

class ConfigError(Exception):
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return repr(self.value)

class PinError(Exception):
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return repr(self.value)


if getattr(settings, 'PIN_ENVIRONMENTS', {}) == {}:
    raise ConfigError("PIN_ENVIRONMENTS not defined.")


TRANS_TYPE_CHOICES = (
    ('Payment', 'Payment'),
    ('Refund', 'Refund'),
)

CARD_TYPES = (
    ('master', 'Mastercard'),
    ('visa', 'Visa'),
)

class CustomerToken(models.Model):
    """
    A token returned by the Pin Payments Customer API.
    These can be used on a Transaction in lieu of of a Card token, and 
    can be reused. 
    They are linked to a User record and are typically used for recurring 
    billing.
    Card token - difference is that a card can only be used once, for a transaction
    or to be converted to a Customer token. Customer tokens can be reused.
    """
    user = models.ForeignKey(User)
    environment = models.CharField(max_length=25, db_index=True, blank=True, help_text=_('The name of the Pin environment to use, eg test or live.'))
    token = models.CharField(_('Token'), max_length=100, help_text=_('Generated by Card API or Customers API'))
    created = models.DateTimeField(_('Created'), auto_now_add=True)
    active = models.BooleanField(_('Active'), default=True)
    card_type = models.CharField(_('Card Type'), max_length=20, blank=True, null=True, choices=CARD_TYPES, help_text=_('Determined automatically by Pin'))
    card_number = models.CharField(_('Card Number'), max_length=100, blank=True, null=True, help_text=_('Cleansed by Pin API'))

    def save(self, *args, **kwargs):
        if not self.environment:
            self.environment = getattr(settings, 'PIN_DEFAULT_ENVIRONMENT', 'test')
        super(CustomerToken, self).save(*args, **kwargs)

    @classmethod
    def create_from_card_token(cls, card_token, user, environment=''):
        pin_config = getattr(settings, 'PIN_ENVIRONMENTS', {})

        payload = {
            'email': user.email,
            'card_token': card_token,
            }
        
        if not environment:
            environment = getattr(settings, 'PIN_DEFAULT_ENVIRONMENT', 'test')

        if environment not in pin_config.keys():
            raise ConfigError("Invalid environment '%s'" % environment)

        pin_env = pin_config[environment]

        (pin_secret, pin_host) = (pin_env.get('secret', None), pin_env.get('host', None))

        if not (pin_secret and pin_host):
            raise ConfigError("Environment '%s' does not have secret and host configured." % environment)

        response = requests.post(
            "https://%s/1/customers" % pin_host,
            auth    = (pin_secret, ''), 
            params  = payload,
            headers = { 'content-type': 'application/json' },
        )

        try:
            r = response.json()
        except:
            r = None

        if r == None:
            raise PinError('Error retrieving response')

        else:
            if r.has_key('error'):
                raise PinError('Error returned from Pin API: %s' % r['error_description'])
            else:
                customer = CustomerToken()
                customer.user           = user
                customer.token          = r['response']['token']
                customer.environment    = environment
                customer.card_number    = r['response']['card']['display_number']
                customer.card_type      = r['response']['card']['scheme']
                customer.save()

                return customer



class PinTransaction(models.Model):
    """
    PinTransaction - model to hold response data from the pin.net.au 
    Charge API. Note we capture the card and/or customer token, but 
    there's no FK to your own customers table. That's for you to do 
    in your own code.
    """
    date = models.DateTimeField(_('Date'), db_index=True, help_text=_('Time this transaction was put in the database. May differ from the time that PIN reports the transaction.'))
    environment = models.CharField(max_length=25, db_index=True, blank=True, help_text=_('The name of the Pin environment to use, eg test or live.'))
    amount = models.DecimalField(_('Amount (Dollars)'), max_digits=10, decimal_places=2)
    fees = models.DecimalField(_('Transaction Fees'), max_digits=10, decimal_places=2, default=0, help_text=_('Fees charged to you by Pin, for this transaction, in dollars'), blank=True, null=True)
    description = models.TextField(_('Description'), blank=True, null=True, help_text=_('As provided when you initiated the transaction'))
    processed = models.BooleanField(_('Processed?'), default=False, help_text=_('Has this been sent to Pin yet?'))
    succeeded = models.BooleanField(_('Success?'), default=False, help_text=_('Was the transaction approved?'))
    currency = models.CharField(_('Currency'), max_length=100, default='AUD', help_text=_('Currency transaction was processed in'))
    transaction_token = models.CharField(_('Pin API Transaction Token'), max_length=100, blank=True, null=True, db_index=True, help_text=_('Unique ID from Pin for this transaction'))
    card_token = models.CharField(_('Pin API Card Token'), max_length=40, blank=True, null=True, help_text=_('Card token used for this transaction (Card API and Web Forms)'))
    customer_token = models.ForeignKey(CustomerToken, blank=True, null=True, help_text=_('Provided by Customer API'))
    pin_response = models.CharField(_('API Response'), max_length=100, blank=True, null=True, help_text=_('Response text, usually Success!'))
    ip_address = models.GenericIPAddressField(help_text=_('IP Address used for payment'))
    email_address = models.EmailField(_('E-Mail Address'), max_length=100, help_text=_('As passed to Pin.'))
    card_address1 = models.CharField(_('Cardholder Street Address'), max_length=100, blank=True, null=True, help_text=_('Address entered by customer to process this transaction'))
    card_address2 = models.CharField(_('Cardholder Street Address Line 2'), max_length=100, blank=True, null=True)
    card_city = models.CharField(_('Cardholder City'), max_length=100, blank=True, null=True)
    card_state = models.CharField(_('Cardholder State'), max_length=100, blank=True, null=True)
    card_postcode = models.CharField(_('Cardholder Postal / ZIP Code'), max_length=100, blank=True, null=True)
    card_country = models.CharField(_('Cardholder Country'), max_length=100, blank=True, null=True)
    card_number = models.CharField(_('Card Number'), max_length=100, blank=True, null=True, help_text=_('Cleansed by Pin API'))
    card_type = models.CharField(_('Card Type'), max_length=20, blank=True, null=True, choices=CARD_TYPES, help_text=_('Determined automatically by Pin'))
    pin_response_text = models.TextField(_('Complete API Response'), blank=True, null=True, help_text=_('The full JSON response from the Pin API'))

    def save(self, *args, **kwargs):
        if not (self.card_token or self.customer_token):
            raise PinError("Must provide card_token or customer_token")

        if self.card_token and self.customer_token:
            raise PinError("Can only provide card_token OR customer_token, not both")

        if not self.environment:
            self.environment = getattr(settings, 'PIN_DEFAULT_ENVIRONMENT', 'test')

        if self.environment not in getattr(settings, 'PIN_ENVIRONMENTS', {}):
            raise PinError("Pin Environment '%s' does not exist" % self.environment)

        if not self.date:
            from datetime import datetime
            self.date = datetime.now()

        super(PinTransaction, self).save(*args, **kwargs)
    
    def __unicode__(self):
        return "%s" % self.id

    class Meta:
        verbose_name = 'PIN.net.au Transaction'
        verbose_name_plural = 'PIN.net.au Transactions'
        ordering = ['-date',]


    def process_transaction(self):
        if self.processed:
            """
            can only attempt to process once.
            """
            return None

        self.processed = True
        self.save()

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

        pin_env = getattr(settings, 'PIN_ENVIRONMENTS', {})[self.environment]

        (pin_secret, pin_host) = (pin_env.get('secret', None), pin_env.get('host', None))

        if not (pin_secret and pin_host):
            raise ConfigError("Environment '%s' does not have secret and host configured." % self.environment)

        response = requests.post(
            "https://%s/1/charges" % pin_host,
            auth    = (pin_secret, ''), 
            params  = payload,
            headers = { 'content-type': 'application/json' },
        )

        try:
            r = response.json()
        except:
            r = None

        self.pin_response_text = response.text,

        if r == None:
            self.pin_response = 'Failure.'
        elif r.has_key('error'):
            if r.has_key('messages'):
                if r['messages'][0].has_key('message'):
                    self.pin_response = 'Failure: %s' % r['messages'][0]['message']
            else:
                self.pin_response = 'Failure: %s' % r['error_description']
            self.transaction_token  = r['charge_token']
        else:
            self.succeeded          = True
            self.transaction_token  = r['response']['token']
            self.fees               = r['response']['total_fees'] / 100.00
            self.pin_response       = r['response']['status_message']
            self.card_address1      = r['response']['card']['address_line1']
            self.card_address2      = r['response']['card']['address_line2']
            self.card_city          = r['response']['card']['address_city']
            self.card_state         = r['response']['card']['address_state']
            self.card_postcode      = r['response']['card']['address_postcode']
            self.card_country       = r['response']['card']['address_country']
            self.card_number        = r['response']['card']['display_number']
            self.card_type          = r['response']['card']['scheme']
        self.save()

        return self.pin_response


