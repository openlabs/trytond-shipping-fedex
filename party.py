# -*- coding: utf-8 -*-
"""
    party.py

    :copyright: (c) 2015 by Openlabs Technologies & Consulting (P) Limited
    :license: BSD, see LICENSE for more details.
"""
from trytond.transaction import Transaction
from trytond.pool import Pool, PoolMeta

__all__ = ['Address']
__metaclass__ = PoolMeta


class Address:
    """
    Party Address
    """
    __name__ = 'party.address'

    def address_to_fedex_dict(self):
        """
        This method creates a dict of address details
        to be used by the FedEx integration API.

        :return: returns the dictionary comprising of the details
                of the package recipient.
        """
        Company = Pool().get('company.company')

        streetlines = []
        company = Company(Transaction().context.get('company'))

        phone = self.party.phone
        if phone:
            # FedEx accepts only numeric numbers in phone
            phone = filter(lambda char: char.isdigit(), phone)
        if self.street:
            streetlines.append(self.street)
        if self.streetbis:
            streetlines.append(self.streetbis)
        return {
            'company_name': company.party.name,
            'person_name': self.name,
            'phone': phone,
            'email': self.party.email,
            'streetlines': streetlines,
            'city': self.city,
            'state_code': self.subdivision and self.subdivision.code,
            'postal_code': self.zip,
            'country_code': self.country and self.country.code,
        }

    def set_fedex_address(self, fedex_object):
        '''
        Computes the details of the shipper or recipient depending on object,
        passes the values to ship request
        '''
        address = self.address_to_fedex_dict()
        fedex_object.Contact.CompanyName = address['company_name']
        fedex_object.Contact.PersonName = address['person_name']
        fedex_object.Contact.PhoneNumber = address['phone']
        fedex_object.Contact.EMailAddress = address['email']
        fedex_object.Address.StreetLines = address['streetlines']
        fedex_object.Address.City = address['city']
        fedex_object.Address.StateOrProvinceCode = address['state_code'][-2:]
        fedex_object.Address.PostalCode = address['postal_code']
        fedex_object.Address.CountryCode = address['country_code']
