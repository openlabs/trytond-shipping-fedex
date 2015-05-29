# -*- coding: utf-8 -*-
"""
    stock.py

    :copyright: (c) 2015 by Openlabs Technologies & Consulting (P) Limited
    :license: BSD, see LICENSE for more details.
"""
from decimal import Decimal
import base64

from trytond.model import ModelView, fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval
from trytond.rpc import RPC

from fedex import RateService, ProcessShipmentRequest
from fedex.exceptions import RequestError


__all__ = [
    'ShipmentOut', 'GenerateFedexLabelMessage', 'GenerateShippingLabel',
]
__metaclass__ = PoolMeta


class ShipmentOut:
    "Shipment Out"
    __name__ = 'stock.shipment.out'

    is_fedex_shipping = fields.Function(
        fields.Boolean('Is Shipping', readonly=True),
        'get_is_fedex_shipping'
    )
    fedex_drop_off_type = fields.Many2One(
        'fedex.shipment.method', 'Default Drop-Off Type',
        domain=[('method_type', '=', 'dropoff')],
        states={
            'required': Eval('is_fedex_shipping', True),
            'readonly': ~Eval('state').in_(['packed', 'done']),
        },
        depends=['is_fedex_shipping', 'state']
    )
    fedex_packaging_type = fields.Many2One(
        'fedex.shipment.method', 'Default Packaging Type',
        domain=[('method_type', '=', 'packaging')],
        states={
            'required': Eval('is_fedex_shipping', True),
            'readonly': ~Eval('state').in_(['packed', 'done']),
        },
        depends=['is_fedex_shipping', 'state']
    )
    fedex_service_type = fields.Many2One(
        'fedex.shipment.method', 'Default Service Type',
        domain=[('method_type', '=', 'service')],
        states={
            'required': Eval('is_fedex_shipping', True),
            'readonly': ~Eval('state').in_(['packed', 'done']),
        },
        depends=['is_fedex_shipping', 'state']
    )

    def get_is_fedex_shipping(self, name):
        """
        Check if shipping is from fedex
        """
        return self.carrier and \
            self.carrier.carrier_cost_method == 'fedex' or False

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

    @classmethod
    def __setup__(cls):
        super(ShipmentOut, cls).__setup__()
        # There can be cases when people might want to use a different
        # shipment carrier after the shipment is marked as done
        cls.carrier.states = {
            'readonly': ~Eval('state').in_(['packed', 'done']),
        }
        cls._error_messages.update({
            'warehouse_address_required': 'Warehouse address is required.',
            'error_label': 'Error in generating label "%s"',
            'fedex_settings_missing':
                'FedEx settings on this sale are missing',
            'tracking_number_already_present':
                'Tracking Number is already present for this shipment.',
            'invalid_state': 'Labels can only be generated when the '
                'shipment is in Packed or Done states only',
            'wrong_carrier': 'Carrier for selected shipment is not FedEx',
            'fedex_shipping_cost_error':
                'Error while getting shipping cost from Fedex: \n\n%s'
        })
        cls.__rpc__.update({
            'make_fedex_labels': RPC(readonly=False, instantiate=0),
            'get_fedex_shipping_cost': RPC(readonly=False, instantiate=0),
        })

    def on_change_carrier(self):
        res = super(ShipmentOut, self).on_change_carrier()

        res['is_fedex_shipping'] = self.carrier and \
            self.carrier.carrier_cost_method == 'fedex'

        return res

    def _get_carrier_context(self):
        "Pass shipment in the context"
        context = super(ShipmentOut, self)._get_carrier_context()

        if not self.carrier.carrier_cost_method == 'fedex':
            return context

        context = context.copy()
        context['shipment'] = self.id
        return context

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
        requested_shipment.PreferredCurrency = self.cost_currency.code

        # Shipper and Recipient
        requested_shipment.Shipper.AccountNumber = \
            fedex_credentials.AccountNumber
        # From location is the warehouse location. So it must be filled.
        if not self.warehouse.address:
            self.raise_user_error('warehouse_address_required')
        self.warehouse.address.set_fedex_address(requested_shipment.Shipper)
        self.delivery_address.set_fedex_address(requested_shipment.Recipient)

        # Shipping Charges Payment
        shipping_charges = requested_shipment.ShippingChargesPayment
        shipping_charges.PaymentType = 'SENDER'
        shipping_charges.Payor.ResponsibleParty = requested_shipment.Shipper

        # Express Freight Detail
        fright_detail = requested_shipment.ExpressFreightDetail
        fright_detail.PackingListEnclosed = 1
        fright_detail.ShippersLoadAndCount = 2
        fright_detail.BookingConfirmationNumber = 'Ref-%s' % self.reference

        # Customs Clearance Detail
        self.get_fedex_customs_details(rate_request)

        # Label Specification
        requested_shipment.LabelSpecification.LabelFormatType = 'COMMON2D'
        requested_shipment.LabelSpecification.ImageType = 'PNG'
        requested_shipment.LabelSpecification.LabelStockType = 'PAPER_4X6'

        requested_shipment.RateRequestTypes = ['ACCOUNT']

        self.get_fedex_items_details(rate_request)

        try:
            response = rate_request.send_request(int(self.id))
        except RequestError, exc:
            self.raise_user_error(
                'fedex_shipping_cost_error', error_args=(exc.message, )
            )

        currency, = Currency.search([
            ('code', '=', str(
                response.RateReplyDetails[0].RatedShipmentDetails[0].
                ShipmentRateDetail.TotalNetCharge.Currency
            ))
        ])

        return Decimal(str(
            response.RateReplyDetails[0].RatedShipmentDetails[0].ShipmentRateDetail.TotalNetCharge.Amount  # noqa
        )), currency.id

    def get_fedex_customs_details(self, fedex_request):
        """
        Computes the details of the customs items and passes to fedex request
        """
        ProductUom = Pool().get('product.uom')

        customs_detail = fedex_request.get_element_from_type(
            'CustomsClearanceDetail'
        )
        customs_detail.DocumentContent = 'DOCUMENTS_ONLY'

        weight_uom, = ProductUom.search([('symbol', '=', 'lb')])

        # Encoding Items for customs
        commodities = []
        customs_value = 0
        for move in self.outgoing_moves:
            if move.product.type == 'service':
                continue
            commodity = fedex_request.get_element_from_type('Commodity')
            commodity.NumberOfPieces = len(self.outgoing_moves)
            commodity.Name = move.product.name
            commodity.Description = move.product.description or \
                move.product.name
            commodity.CountryOfManufacture = \
                self.warehouse.address.country.code
            commodity.Weight.Units = 'LB'
            commodity.Weight.Value = int(move.get_weight(weight_uom))
            commodity.Quantity = int(move.quantity)
            commodity.QuantityUnits = 'EA'
            commodity.UnitPrice.Amount = int(move.unit_price)
            commodity.UnitPrice.Currency = self.company.currency.code
            commodity.CustomsValue.Currency = self.company.currency.code
            commodity.CustomsValue.Amount = int(
                Decimal(str(move.quantity)) * move.unit_price
            )
            commodities.append(commodity)
            customs_value += Decimal(str(move.quantity)) * move.unit_price

        customs_detail.CustomsValue.Currency = self.company.currency.code
        customs_detail.CustomsValue.Amount = int(customs_value)

        # Commercial Invoice
        customs_detail.CommercialInvoice.TermsOfSale = 'FOB'
        customs_detail.DutiesPayment.PaymentType = 'SENDER'
        customs_detail.DutiesPayment.Payor.ResponsibleParty = \
            fedex_request.RequestedShipment.Shipper

        fedex_request.RequestedShipment.CustomsClearanceDetail = customs_detail
        fedex_request.RequestedShipment.CustomsClearanceDetail.Commodities = \
            commodities

    def make_fedex_labels(self):
        """
        Make labels for the given shipment

        :return: Tracking number as string
        """
        Currency = Pool().get('currency.currency')
        Attachment = Pool().get('ir.attachment')
        Package = Pool().get('stock.package')
        Uom = Pool().get('product.uom')

        if self.state not in ('packed', 'done'):
            self.raise_user_error('invalid_state')

        if not self.carrier.carrier_cost_method == 'fedex':
            self.raise_user_error('wrong_carrier')

        if self.tracking_number:
            self.raise_user_error('tracking_number_already_present')

        fedex_credentials = self.carrier.get_fedex_credentials()

        ship_request = ProcessShipmentRequest(fedex_credentials)
        requested_shipment = ship_request.RequestedShipment

        requested_shipment.DropoffType = self.fedex_drop_off_type.value
        requested_shipment.ServiceType = self.fedex_service_type.value
        requested_shipment.PackagingType = self.fedex_packaging_type.value

        uom_pound, = Uom.search([('symbol', '=', 'lb')])

        if len(self.packages) > 1:
            requested_shipment.TotalWeight.Units = 'LB'
            requested_shipment.TotalWeight.Value = Uom.compute_qty(
                self.weight_uom, self.weight, uom_pound
            )

        # Shipper & Recipient
        requested_shipment.Shipper.AccountNumber = \
            fedex_credentials.AccountNumber

        if not self.warehouse.address:
            self.raise_user_error('warehouse_address_required')

        self.warehouse.address.set_fedex_address(requested_shipment.Shipper)
        self.delivery_address.set_fedex_address(requested_shipment.Recipient)

        # Shipping Charges Payment
        shipping_charges = requested_shipment.ShippingChargesPayment
        shipping_charges.PaymentType = 'SENDER'
        shipping_charges.Payor.ResponsibleParty = requested_shipment.Shipper

        # Express Freight Detail
        fright_detail = requested_shipment.ExpressFreightDetail
        fright_detail.PackingListEnclosed = 1  # XXX
        fright_detail.ShippersLoadAndCount = 2  # XXX
        fright_detail.BookingConfirmationNumber = 'Ref-%s' % self.reference

        if self.is_international_shipping:
            # Customs Clearance Detail
            self.get_fedex_customs_details(ship_request)

        # Label Specification
        # Maybe make them as configurable items in later versions
        requested_shipment.LabelSpecification.LabelFormatType = 'COMMON2D'
        requested_shipment.LabelSpecification.ImageType = 'PNG'
        requested_shipment.LabelSpecification.LabelStockType = 'PAPER_4X6'

        requested_shipment.RateRequestTypes = ['ACCOUNT']

        master_tracking_number = None

        for index, package in enumerate(self.packages, start=1):
            item = ship_request.get_element_from_type(
                'RequestedPackageLineItem'
            )
            item.SequenceNumber = index

            # TODO: some country needs item.ItemDescription

            item.Weight.Units = 'LB'
            item.Weight.Value = Uom.compute_qty(
                package.weight_uom, package.weight, uom_pound
            )

            ship_request.RequestedShipment.RequestedPackageLineItems = [item]
            ship_request.RequestedShipment.PackageCount = len(self.packages)

            if master_tracking_number is not None:
                tracking_id = ship_request.get_element_from_type(
                    'TrackingId'
                )
                tracking_id.TrackingNumber = master_tracking_number
                ship_request.RequestedShipment.MasterTrackingId = tracking_id

            try:
                response = ship_request.send_request(str(self.id))
            except RequestError, error:
                self.raise_user_error('error_label', error_args=(error,))

            package_details = response.CompletedShipmentDetail.CompletedPackageDetails  # noqa
            tracking_number = package_details[0].TrackingIds[0].TrackingNumber

            if self.packages.index(package) == 0:
                master_tracking_number = tracking_number

            Package.write([package], {
                'tracking_number': tracking_number,
            })

            for id, image in enumerate(package_details[0].Label.Parts):
                Attachment.create([{
                    'name': "%s_%s_Fedex.png" % (tracking_number, id),
                    'type': 'data',
                    'data': buffer(base64.decodestring(image.Image)),
                    'resource': '%s,%s' % (self.__name__, self.id)
                }])

        currency, = Currency.search([
            ('code', '=', str(
                response.CompletedShipmentDetail.ShipmentRating.
                ShipmentRateDetails[0].TotalNetCharge.Currency
            ))
        ])
        self.__class__.write([self], {
            'cost': Decimal(str(
                response.CompletedShipmentDetail.ShipmentRating.
                ShipmentRateDetails[0].TotalNetCharge.Amount
            )),
            'cost_currency': currency,
            'tracking_number': master_tracking_number,
        })

        return master_tracking_number


class GenerateFedexLabelMessage(ModelView):
    'Generate Fedex Labels Message'
    __name__ = 'generate.fedex.label.message'

    tracking_number = fields.Char("Tracking number", readonly=True)


class GenerateShippingLabel:
    'Generate Labels'
    __name__ = 'shipping.label'

    def transition_next(self):
        state = super(GenerateShippingLabel, self).transition_next()

        if self.start.carrier.carrier_cost_method == 'fedex':
            return 'generate'
        return state
