# -*- coding: utf-8 -*-
"""
    sale.py

    :copyright: (c) 2015 by Openlabs Technologies & Consulting (P) Limited
    :license: BSD, see LICENSE for more details.
"""
from decimal import Decimal

from trytond.model import fields, ModelView
from trytond.pool import PoolMeta, Pool
from trytond.pyson import Eval
from trytond.transaction import Transaction

from fedex import RateService
from fedex.exceptions import RequestError

__all__ = ['Configuration', 'Sale']
__metaclass__ = PoolMeta


class Configuration:
    'Sale Configuration'
    __name__ = 'sale.configuration'

    fedex_drop_off_type = fields.Many2One(
        'fedex.shipment.method', 'Default Drop-Off Type',
        domain=[('method_type', '=', 'dropoff')],
    )
    fedex_packaging_type = fields.Many2One(
        'fedex.shipment.method', 'Default Packaging Type',
        domain=[('method_type', '=', 'packaging')],
    )
    fedex_service_type = fields.Many2One(
        'fedex.shipment.method', 'Default Service Type',
        domain=[('method_type', '=', 'service')],
    )


class Sale:
    "Sale"
    __name__ = 'sale.sale'

    is_fedex_shipping = fields.Function(
        fields.Boolean('Is Fedex Shipping'),
        'get_is_fedex_shipping',
    )
    fedex_drop_off_type = fields.Many2One(
        'fedex.shipment.method', 'Default Drop-Off Type',
        domain=[('method_type', '=', 'dropoff')],
        states={
            'required': Eval('is_fedex_shipping', True),
            'readonly': ~Eval('state').in_(['draft', 'quotation']),
        },
        depends=['is_fedex_shipping', 'state']
    )
    fedex_packaging_type = fields.Many2One(
        'fedex.shipment.method', 'Default Packaging Type',
        domain=[('method_type', '=', 'packaging')],
        states={
            'required': Eval('is_fedex_shipping', True),
            'readonly': ~Eval('state').in_(['draft', 'quotation']),
        },
        depends=['is_fedex_shipping', 'state']
    )
    fedex_service_type = fields.Many2One(
        'fedex.shipment.method', 'Default Service Type',
        domain=[('method_type', '=', 'service')],
        states={
            'required': Eval('is_fedex_shipping', True),
            'readonly': ~Eval('state').in_(['draft', 'quotation']),
        },
        depends=['is_fedex_shipping', 'state']
    )

    def get_is_fedex_shipping(self, name):
        return self.carrier and \
            self.carrier.carrier_cost_method == 'fedex' or False

    @classmethod
    def __setup__(self):
        super(Sale, self).__setup__()
        self._error_messages.update({
            'warehouse_address_required': 'Warehouse address is required.',
            'fedex_settings_missing': 'FedEx settings on this sale are missing',
            'fedex_rates_error':
                "Error while getting rates from Fedex: \n\n%s"
        })
        self._buttons.update({
            'update_fedex_shipment_cost': {
                'invisible': Eval('state') != 'quotation'
            }
        })

    def on_change_carrier(self):
        """
        Show/Hide UPS Tab in view on change of carrier
        """
        res = super(Sale, self).on_change_carrier()

        res['is_fedex_shipping'] = self.carrier and \
            self.carrier.carrier_cost_method == 'fedex'

        return res

    @staticmethod
    def default_fedex_drop_off_type():
        Config = Pool().get('sale.configuration')

        config = Config(1)
        return config.fedex_drop_off_type and config.fedex_drop_off_type.id

    @staticmethod
    def default_fedex_packaging_type():
        Config = Pool().get('sale.configuration')

        config = Config(1)
        return config.fedex_packaging_type and config.fedex_packaging_type.id

    @staticmethod
    def default_fedex_service_type():
        Config = Pool().get('sale.configuration')

        config = Config(1)
        return config.fedex_service_type and config.fedex_service_type.id

    def _get_carrier_context(self):
        "Pass sale in the context"
        # XXX: This override should not be here, it should be in
        # trytond-shipping

        context = super(Sale, self)._get_carrier_context()

        if not self.carrier.carrier_cost_method == 'fedex':
            return context

        context = context.copy()
        context['sale'] = self.id
        return context

    def on_change_lines(self):
        """Pass a flag in context which indicates the get_sale_price method
        of FedEx carrier not to calculate cost on each line change
        """
        with Transaction().set_context({'ignore_carrier_computation': True}):
            return super(Sale, self).on_change_lines()

    def apply_fedex_shipping(self):
        "Add a shipping line to sale for fedex"
        Currency = Pool().get('currency.currency')

        if self.is_fedex_shipping:
            with Transaction().set_context(self._get_carrier_context()):
                shipment_cost, currency_id = self.carrier.get_sale_price()
                if not shipment_cost:
                    return
            # Convert the shipping cost to sale currency from USD
            shipment_cost = Currency.compute(
                Currency(currency_id), shipment_cost, self.currency
            )
            self.add_shipping_line(
                shipment_cost,
                "%s - %s" % (
                    self.carrier.party.name, self.fedex_packaging_type.name
                )
            )

    @classmethod
    def quote(cls, sales):
        res = super(Sale, cls).quote(sales)
        cls.update_fedex_shipment_cost(sales)
        return res

    @classmethod
    @ModelView.button
    def update_fedex_shipment_cost(cls, sales):
        for sale in sales:
            sale.apply_fedex_shipping()

    def get_fedex_shipping_cost(self):
        """Returns the calculated shipping cost as sent by fedex
        :returns: The shipping cost in USD
        """
        Currency = Pool().get('currency.currency')

        fedex_credentials = self.carrier.get_fedex_credentials()

        if not all([
            self.fedex_drop_off_type, self.fedex_packaging_type,
            self.fedex_service_type
        ]):
            self.raise_user_error('fedex_settings_missing')

        rate_request = RateService(fedex_credentials)
        requested_shipment = rate_request.RequestedShipment

        requested_shipment.DropoffType = self.fedex_drop_off_type.value
        requested_shipment.ServiceType = self.fedex_service_type.value
        requested_shipment.PackagingType = self.fedex_packaging_type.value
        requested_shipment.PreferredCurrency = self.currency.code

        # Shipper and Recipient
        requested_shipment.Shipper.AccountNumber = \
            fedex_credentials.AccountNumber

        ship_from_address = self._get_ship_from_address()
        # From location is the warehouse location. So it must be filled.
        if ship_from_address is None:
            self.raise_user_error('warehouse_address_required')

        ship_from_address.set_fedex_address(requested_shipment.Shipper)
        self.shipment_address.set_fedex_address(requested_shipment.Recipient)

        # Shipping Charges Payment
        shipping_charges = requested_shipment.ShippingChargesPayment
        shipping_charges.PaymentType = 'SENDER'
        shipping_charges.Payor.ResponsibleParty = requested_shipment.Shipper

        # Express Freight Detail
        fright_detail = requested_shipment.ExpressFreightDetail

        # If you enclose a packing list with your freight shipment, this element
        # informs FedEx operations that shipment contents can be verified on
        # your packing list.
        fright_detail.PackingListEnclosed = 1

        fright_detail.BookingConfirmationNumber = 'Ref-%s' % self.reference

        if self.is_international_shipping:
            # Customs Clearance Detail
            self.get_fedex_customs_details(rate_request)

        # Label Specification
        # Maybe make them as configurable items in later versions
        requested_shipment.LabelSpecification.LabelFormatType = 'COMMON2D'
        requested_shipment.LabelSpecification.ImageType = 'PNG'
        requested_shipment.LabelSpecification.LabelStockType = 'PAPER_4X6'

        requested_shipment.RateRequestTypes = ['ACCOUNT']

        self.get_fedex_items_details(rate_request)

        try:
            response = rate_request.send_request(int(self.id))
        except RequestError, exc:
            self.raise_user_error(
                'fedex_rates_error', error_args=(exc.message, )
            )

        currency, = Currency.search([
            ('code', '=', str(
                response.RateReplyDetails[0].RatedShipmentDetails[0].
                ShipmentRateDetail.TotalNetCharge.Currency
            ))
        ])

        return Decimal(str(
            response.RateReplyDetails[0].RatedShipmentDetails[0].
            ShipmentRateDetail.TotalNetCharge.Amount)
        ), currency.id

    def get_fedex_customs_details(self, fedex_request):
        """
        Computes the details of the customs items and passes to fedex request
        """
        ProductUom = Pool().get('product.uom')

        customs_detail = fedex_request.get_element_from_type(
            'CustomsClearanceDetail'
        )
        customs_detail.DocumentContent = 'DOCUMENTS_ONLY'

        # Encoding Items for customs
        commodities = []
        customs_value = 0
        for line in self.lines:
            if line.product.type == 'service':
                continue

            weight_uom, = ProductUom.search([('symbol', '=', 'lb')])

            commodity = fedex_request.get_element_from_type('Commodity')
            commodity.NumberOfPieces = len(self.lines)
            commodity.Name = line.product.name
            commodity.Description = line.description
            commodity.CountryOfManufacture = \
                self.warehouse.address.country.code
            commodity.Weight.Units = 'LB'
            commodity.Weight.Value = line.get_weight(weight_uom)
            commodity.Quantity = int(line.product.quantity)
            commodity.QuantityUnits = 'EA'
            commodity.UnitPrice.Amount = int(line.unit_price)
            commodity.UnitPrice.Currency = self.company.currency.code
            commodity.CustomsValue.Currency = self.company.currency.code
            commodity.CustomsValue.Amount = int(
                Decimal(str(line.quantity)) * line.unit_price
            )
            commodities.append(commodity)
            customs_value += Decimal(str(line.quantity)) * line.unit_price

        customs_detail.CustomsValue.Currency = self.company.currency.code
        customs_detail.CustomsValue.Amount = int(customs_value)

        fedex_request.RequestedShipment.CustomsClearanceDetail = customs_detail
        fedex_request.RequestedShipment.CustomsClearanceDetail.Commodities = \
            commodities

        # Commercial Invoice
        customs_detail.CommercialInvoice.TermsOfSale = 'FOB_OR_FCA'
        customs_detail.DutiesPayment.PaymentType = 'SENDER'
        customs_detail.DutiesPayment.Payor.ResponsibleParty = \
            fedex_request.RequestedShipment.Shipper

    def get_fedex_items_details(self, fedex_request):
        '''
        Computes the details of the shipment items and passes to fedex request
        '''
        ProductUom = Pool().get('product.uom')

        item = fedex_request.get_element_from_type(
            'RequestedPackageLineItem'
        )
        weight_uom, = ProductUom.search([('symbol', '=', 'lb')])
        item.SequenceNumber = 1
        item.Weight.Units = 'LB'
        item.Weight.Value = ProductUom.compute_qty(
            self.weight_uom, self.package_weight, weight_uom
        )

        # From sale you cannot define packages per shipment, so single
        # package per shipment.
        item.GroupPackageCount = 1
        fedex_request.RequestedShipment.PackageCount = 1

        fedex_request.RequestedShipment.RequestedPackageLineItems = [item]

    def create_shipment(self, shipment_type):
        Shipment = Pool().get('stock.shipment.out')

        with Transaction().set_context(ignore_carrier_computation=True):
            shipments = super(Sale, self).create_shipment(shipment_type)

        if shipment_type == 'out' and shipments and self.is_fedex_shipping:
            Shipment.write(shipments, {
                'fedex_drop_off_type': self.fedex_drop_off_type.id,
                'fedex_packaging_type': self.fedex_packaging_type.id,
                'fedex_service_type': self.fedex_service_type.id,
            })
        return shipments
