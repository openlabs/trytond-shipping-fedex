# -*- coding: utf-8 -*-
"""
    tests/conftest.py

    :copyright: (C) 2015 by Openlabs Technologies & Consulting (P) Limited
    :license: BSD, see LICENSE for more details.
"""
import os
import time
import datetime
from decimal import Decimal
from collections import namedtuple
from dateutil.relativedelta import relativedelta

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--db", action="store", default="sqlite",
        help="Run on database: sqlite or postgres"
    )


@pytest.fixture(scope='session', autouse=True)
def install_module(request):
    """Install tryton module in specified database.
    """
    if request.config.getoption("--db") == 'sqlite':
        os.environ['TRYTOND_DATABASE_URI'] = "sqlite://"
        os.environ['DB_NAME'] = ':memory:'

    elif request.config.getoption("--db") == 'postgres':
        os.environ['TRYTOND_DATABASE_URI'] = "postgresql://"
        os.environ['DB_NAME'] = 'test_' + str(int(time.time()))

    from trytond.tests import test_tryton
    test_tryton.install_module('shipping_fedex')


@pytest.yield_fixture()
def transaction(request):
    """Yields transaction with installed module.
    """
    from trytond.transaction import Transaction
    from trytond.tests.test_tryton import USER, CONTEXT, DB_NAME, POOL

    # Inject helper functions in instance on which test function was collected.
    request.instance.POOL = POOL
    request.instance.USER = USER
    request.instance.CONTEXT = CONTEXT
    request.instance.DB_NAME = DB_NAME

    with Transaction().start(DB_NAME, USER, context=CONTEXT) as transaction:
        yield transaction

        transaction.cursor.rollback()


@pytest.fixture(scope='session')
def dataset(request):
    """Create minimal data needed for testing
    """
    from trytond.transaction import Transaction
    from trytond.tests.test_tryton import USER, CONTEXT, DB_NAME, POOL

    Party = POOL.get('party.party')
    Company = POOL.get('company.company')
    Country = POOL.get('country.country')
    Subdivision = POOL.get('country.subdivision')
    Employee = POOL.get('company.employee')
    Currency = POOL.get('currency.currency')
    SequenceStrict = POOL.get('ir.sequence.strict')
    User = POOL.get('res.user')
    FiscalYear = POOL.get('account.fiscalyear')
    Sequence = POOL.get('ir.sequence')
    AccountTemplate = POOL.get('account.account.template')
    Account = POOL.get('account.account')
    ProductTemplate = POOL.get('product.template')
    Product = POOL.get('product.product')
    Uom = POOL.get('product.uom')
    Carrier = POOL.get('carrier')
    PaymentTerm = POOL.get('account.invoice.payment_term')
    FedExShipmentMethod = POOL.get('fedex.shipment.method')
    StockLocation = POOL.get('stock.location')
    SaleConfiguration = POOL.get('sale.configuration')
    AccountCreateChart = POOL.get('account.create_chart', type="wizard")

    with Transaction().start(DB_NAME, USER, context=CONTEXT) as transaction:
        # Create company, employee and set it user's current company
        usd, = Currency.create([{
            'name': 'US Dollar',
            'code': 'USD',
            'symbol': '$',
        }])

        country_us, = Country.create([{
            'name': 'United States',
            'code': 'US',
        }])
        subdivision_florida, = Subdivision.create([{
            'name': 'Florida',
            'code': 'US-FL',
            'country': country_us.id,
            'type': 'state'
        }])
        subdivision_california, = Subdivision.create([{
            'name': 'California',
            'code': 'US-CA',
            'country': country_us.id,
            'type': 'state'
        }])

        company_party, = Party.create([{
            'name': 'ABC Corp.',
            'addresses': [('create', [{
                'name': 'ABC Corp.',
                'street': '247 High Street',
                'zip': '94301-1041',
                'city': 'Palo Alto',
                'country': country_us.id,
                'subdivision': subdivision_california.id,
            }])],
            'contact_mechanisms': [('create', [{
                'type': 'phone',
                'value': '123456789'
            }])]
        }])

        employee_party, = Party.create([{
            'name': 'Prakash Pandey',
        }])
        company, = Company.create([{
            'party': company_party.id,
            'currency': usd.id,
        }])
        employee, = Employee.create([{
            'party': employee_party.id,
            'company': company.id,
        }])
        User.write(
            [User(USER)], {
                'main_company': company.id,
                'company': company.id,
            }
        )
        CONTEXT.update(User.get_preferences(context_only=True))

        # Set warehouse address as company address
        StockLocation.write(
            StockLocation.search([('type', '=', 'warehouse')]), {
                'address': company_party.addresses[0].id,
            }
        )

        # Create fiscal year
        date = datetime.date.today()

        post_move_sequence, = Sequence.create([{
            'name': '%s' % date.year,
            'code': 'account.move',
            'company': company.id,
        }])
        invoice_sequence, = SequenceStrict.create([{
            'name': '%s' % date.year,
            'code': 'account.invoice',
            'company': company.id,
        }])

        fiscal_year, = FiscalYear.create([{
            'name': '%s' % date.year,
            'start_date': date + relativedelta(month=1, day=1),
            'end_date': date + relativedelta(month=12, day=31),
            'company': company.id,
            'post_move_sequence': post_move_sequence.id,
            'out_invoice_sequence': invoice_sequence.id,
            'in_invoice_sequence': invoice_sequence.id,
            'out_credit_note_sequence': invoice_sequence.id,
            'in_credit_note_sequence': invoice_sequence.id,
        }])
        FiscalYear.create_period([fiscal_year])

        # Create minimal chart of account
        account_template, = AccountTemplate.search(
            [('parent', '=', None)]
        )

        session_id, _, _ = AccountCreateChart.create()
        create_chart = AccountCreateChart(session_id)
        create_chart.account.account_template = account_template
        create_chart.account.company = company
        create_chart.transition_create_account()

        receivable, = Account.search([
            ('kind', '=', 'receivable'),
            ('company', '=', company.id),
        ])
        payable, = Account.search([
            ('kind', '=', 'payable'),
            ('company', '=', company.id),
        ])
        create_chart.properties.company = company
        create_chart.properties.account_receivable = receivable
        create_chart.properties.account_payable = payable
        create_chart.transition_create_properties()

        account_revenue, = Account.search([
            ('kind', '=', 'revenue')
        ])

        # Create payment term
        payment_term, = PaymentTerm.create([{
            'name': 'Direct',
            'lines': [
                ('create', [{
                    'type': 'remainder'
                }])
            ]
        }])

        # Create Products
        uom_pound, = Uom.search([('symbol', '=', 'lb')])
        uom_unit, = Uom.search([('symbol', '=', 'u')])
        uom_day, = Uom.search([('symbol', '=', 'd')])

        product_template, = ProductTemplate.create([{
            'name': 'KindleFire',
            'type': 'goods',
            'salable': True,
            'sale_uom': uom_unit.id,
            'list_price': Decimal('119'),
            'cost_price': Decimal('100'),
            'default_uom': uom_unit.id,
            'account_revenue': account_revenue.id,
            'weight': .7,
            'weight_uom': uom_pound.id,
            'products': [],
        }])

        product1, product2 = Product.create([{
            'code': 'ABC',
            'template': product_template.id,
        }, {
            'code': 'MAD',
            'template': product_template.id,
        }])

        # Create carrier
        carrier_party, = Party.create([{
            'name': 'FedEx',
        }])
        fedex_carrier_product_template, = ProductTemplate.create([{
            'name': 'FedEx Carrier Product',
            'type': 'service',
            'salable': True,
            'sale_uom': uom_day,
            'list_price': Decimal('0'),
            'cost_price': Decimal('0'),
            'default_uom': uom_day,
            'cost_price_method': 'fixed',
            'account_revenue': account_revenue.id,
            'products': [('create', [{
                'code': '001',
            }])]
        }])
        fedex_carrier_product, = fedex_carrier_product_template.products

        fedex_carrier, = Carrier.create([{
            'party': carrier_party.id,
            'carrier_product': fedex_carrier_product.id,
            'carrier_cost_method': 'fedex',
            'fedex_key': 'w8B7YBVgtfnDgn0k',
            'fedex_account_number': '510088000',
            'fedex_password': 'blDSZptRcXwqg3VTSJcU9xNbc',
            'fedex_meter_number': '118518591',
            'fedex_integrator_id': '123',
            'fedex_product_id': 'TEST',
            'fedex_product_version': '9999',
        }])

        # Create customer
        customer, = Party.create([{
            'name': 'John Doe',
            'addresses': [('create', [{
                'name': 'John Doe',
                'street': '250 NE 25th St',
                'zip': '33137',
                'city': 'Miami, Miami-Dade',
                'country': country_us.id,
                'subdivision': subdivision_florida.id,
            }])],
            'contact_mechanisms': [('create', [{
                'type': 'phone',
                'value': '123456789'
            }])]
        }])

        def get_fedex_drop_off_type(value):
            res, = FedExShipmentMethod.search([
                ('method_type', '=', 'dropoff'),
                ('value', '=', value),
            ])
            return res.id

        def get_fedex_packaging_type(value):
            res, = FedExShipmentMethod.search([
                ('method_type', '=', 'packaging'),
                ('value', '=', value),
            ])
            return res.id

        def get_fedex_service_type(value):
            res, = FedExShipmentMethod.search([
                ('method_type', '=', 'service'),
                ('value', '=', value),
            ])
            return res.id

        sale_config = SaleConfiguration(1)
        sale_config.fedex_drop_off_type = \
            get_fedex_drop_off_type('REGULAR_PICKUP')
        sale_config.fedex_packaging_type = \
            get_fedex_packaging_type('FEDEX_BOX')
        sale_config.fedex_service_type = get_fedex_service_type('FEDEX_2_DAY')
        sale_config.save()

        result = {
            'customer': customer,
            'company': company,
            'product1': product1,
            'product2': product2,
            'fedex_carrier': fedex_carrier,
            'currency_usd': usd,
            'payment_term': payment_term,
            'uom_unit': uom_unit,
            'get_fedex_drop_off_type': get_fedex_drop_off_type,
            'get_fedex_packaging_type': get_fedex_packaging_type,
            'get_fedex_service_type': get_fedex_service_type,
        }

        transaction.cursor.commit()

    def get():
        from trytond.model import Model

        for key, value in result.iteritems():
            if isinstance(value, Model):
                result[key] = value.__class__(value.id)
        return namedtuple('Dataset', result.keys())(**result)

    return get
