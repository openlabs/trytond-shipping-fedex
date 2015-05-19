# -*- coding: utf-8 -*-
"""
    tests/test_shipping.py

    :copyright: (C) 2015 by Openlabs Technologies & Consulting (P) Limited
    :license: BSD, see LICENSE for more details.
"""
from decimal import Decimal

from trytond.transaction import Transaction
from trytond.config import config
config.set('database', 'path', '/tmp')


class TestShipping:

    def test_fedex_rates(self, dataset, transaction):
        """A sale having carrier fedex, in quote state gets rate from fedex.
        """
        Sale = self.POOL.get('sale.sale')
        Line = self.POOL.get('sale.line')

        data = dataset()

        sale, = Sale.create([{
            'party': data.customer.id,
            'invoice_address': data.customer.addresses[0].id,
            'shipment_address': data.customer.addresses[0].id,
            'company': data.company.id,
            'currency': data.currency_usd.id,
            'carrier': data.fedex_carrier.id,
            'payment_term': data.payment_term.id,
            'fedex_drop_off_type':
                data.get_fedex_drop_off_type('REGULAR_PICKUP'),
            'fedex_packaging_type':
                data.get_fedex_packaging_type('FEDEX_BOX'),
            'fedex_service_type': data.get_fedex_service_type('FEDEX_2_DAY'),
            'lines': [('create', [{
                'type': 'line',
                'quantity': 1,
                'product': data.product1.id,
                'unit_price': Decimal('119.00'),
                'description': 'KindleFire',
                'unit': data.uom_unit.id,
            }])]
        }])

        sale_line, = sale.lines

        # Quote the sale
        Sale.quote([sale])

        # Shipment line has been added
        assert len(sale.lines) == 2

        shipment_line, = Line.search([('id', '!=', sale_line.id)])

        assert shipment_line.product == data.fedex_carrier.carrier_product
        assert shipment_line.unit_price > Decimal('0')

    def test_fedex_labels_single_package(self, dataset, transaction):
        """Generate fedex label if there is single package.
        """
        Sale = self.POOL.get('sale.sale')
        Attachment = self.POOL.get('ir.attachment')
        GenerateLabel = self.POOL.get('shipping.label', type="wizard")

        data = dataset()

        sale, = Sale.create([{
            'party': data.customer.id,
            'invoice_address': data.customer.addresses[0].id,
            'shipment_address': data.customer.addresses[0].id,
            'company': data.company.id,
            'currency': data.currency_usd.id,
            'carrier': data.fedex_carrier.id,
            'payment_term': data.payment_term.id,
            'fedex_drop_off_type':
                data.get_fedex_drop_off_type('REGULAR_PICKUP'),
            'fedex_packaging_type':
                data.get_fedex_packaging_type('FEDEX_BOX'),
            'fedex_service_type': data.get_fedex_service_type('FEDEX_2_DAY'),
            'lines': [('create', [{
                'type': 'line',
                'quantity': 1,
                'product': data.product1.id,
                'unit_price': Decimal('119.00'),
                'description': 'KindleFire',
                'unit': data.uom_unit.id,
            }])]
        }])

        # Quote, confirm and process
        Sale.quote([sale])
        Sale.confirm([sale])
        Sale.process([sale])

        shipment, = sale.shipments

        # Assign, pack and generate labels.
        shipment.assign([shipment])
        shipment.pack([shipment])

        assert shipment.cost == Decimal('0')

        # There are no label generated yet
        assert Attachment.search([], count=True) == 0

        with Transaction().set_context(
            company=data.company.id, active_id=shipment.id
        ):
            # Call method to generate labels.
            session_id, start_state, _ = GenerateLabel.create()

            generate_label = GenerateLabel(session_id)

            result = generate_label.default_start({})

            assert result['shipment'] == shipment.id
            assert result['carrier'] == shipment.carrier.id
            assert result['no_of_packages'] == 0

            generate_label.start.shipment = shipment.id
            generate_label.start.override_weight = Decimal('0')
            generate_label.start.carrier = result['carrier']

            generate_label.transition_next()
            result = generate_label.default_generate({})

        package, = shipment.packages

        assert package.tracking_number == shipment.tracking_number
        assert Attachment.search([], count=True) == 1
        assert shipment.cost > Decimal('0')

    def test_fedex_labels_multiple_package(self, dataset, transaction):
        """Generate fedex label if there are multiple packages.
        """
        Sale = self.POOL.get('sale.sale')
        Attachment = self.POOL.get('ir.attachment')
        Package = self.POOL.get('stock.package')
        ModelData = self.POOL.get('ir.model.data')
        GenerateLabel = self.POOL.get('shipping.label', type="wizard")

        data = dataset()

        sale, = Sale.create([{
            'party': data.customer.id,
            'invoice_address': data.customer.addresses[0].id,
            'shipment_address': data.customer.addresses[0].id,
            'company': data.company.id,
            'currency': data.currency_usd.id,
            'carrier': data.fedex_carrier.id,
            'payment_term': data.payment_term.id,
            'fedex_drop_off_type':
                data.get_fedex_drop_off_type('REGULAR_PICKUP'),
            'fedex_packaging_type':
                data.get_fedex_packaging_type('FEDEX_BOX'),
            'fedex_service_type': data.get_fedex_service_type('FEDEX_2_DAY'),
            'lines': [('create', [{
                'type': 'line',
                'quantity': 1,
                'product': data.product1.id,
                'unit_price': Decimal('119.00'),
                'description': 'KindleFire',
                'unit': data.uom_unit.id,
            }, {
                'type': 'line',
                'quantity': 2,
                'product': data.product2.id,
                'unit_price': Decimal('119.00'),
                'description': 'KindleFire HD',
                'unit': data.uom_unit.id,
            }])]
        }])

        # Quote, confirm and process
        Sale.quote([sale])
        Sale.confirm([sale])
        Sale.process([sale])

        shipment, = sale.shipments

        type_id = ModelData.get_id(
            "shipping", "shipment_package_type"
        )
        package1, package2 = Package.create([{
            'shipment': '%s,%d' % (shipment.__name__, shipment.id),
            'type': type_id,
            'moves': [('add', [shipment.outgoing_moves[0]])],
        }, {
            'shipment': '%s,%d' % (shipment.__name__, shipment.id),
            'type': type_id,
            'moves': [('add', [shipment.outgoing_moves[1]])],
        }])

        # Assign, pack and generate labels.
        shipment.assign([shipment])
        shipment.pack([shipment])

        assert shipment.cost == Decimal('0')

        # There are no label generated yet
        assert Attachment.search([], count=True) == 0

        with Transaction().set_context(
            company=data.company.id, active_id=shipment.id
        ):
            # Call method to generate labels.
            session_id, start_state, _ = GenerateLabel.create()

            generate_label = GenerateLabel(session_id)

            result = generate_label.default_start({})

            assert result['shipment'] == shipment.id
            assert result['carrier'] == shipment.carrier.id
            assert result['no_of_packages'] == 2

            generate_label.start.shipment = shipment.id
            generate_label.start.override_weight = Decimal('0')
            generate_label.start.carrier = result['carrier']

            generate_label.transition_next()
            result = generate_label.default_generate({})

        assert package1.tracking_number is not None
        assert package2.tracking_number is not None
        assert shipment.tracking_number is not None
        assert Attachment.search([], count=True) == 2
        assert shipment.cost > Decimal('0')
