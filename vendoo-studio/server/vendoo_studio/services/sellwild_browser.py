"""The account choices on Sellwild's sell page, without a Chrome connection."""
from __future__ import annotations

from vendoo_studio.services.account_browser import AccountConnectionError, PrivateAccountWindow

_session = PrivateAccountWindow('Sellwild', 'https://sellwild.com', '/sell')
install = _session.install
connect = _session.connect
shutdown = _session.shutdown

# Select metadata inside the page: neither the full profile nor cookies cross
# into Python. These are the same three GETs used by Sellwild's sell page.
_READS = {
    'address': ('/api/addresses', """(() => {
      if (body.success !== true || !Array.isArray(body.addresses)) return null;
      const addresses = body.addresses.slice().reverse();
      const address = addresses.find(a => Number(a.isDefault) === 1) || addresses[0];
      if (!address?.line1) return [{ship_from:''}];
      return [{ship_from:[address.fullName, address.line1, address.line2,
        `${address.locality}, ${address.region}, ${address.zipCode}`, 'United States of America']
        .map(line => String(line || '').trim()).filter(Boolean).join('\\n')}];
    })()"""),
    'parcel': ('/api/user/profile', "[{default_parcel:String(body.profile?.defaultParcelTemplate || body.defaultParcelTemplate || '')}]"),
    'coupons': ('/api/seller/coupons?limit=50', """(() => {
      const rows = body.coupons || body.data || (Array.isArray(body) ? body : []);
      return Array.isArray(rows) ? rows.filter(row => row.id != null).map(row => ({
        id:row.id, name:String(row.name || row.code || ''),
        discountType:row.discountType || row.discount_type || '',
        discountValue:row.discountValue ?? row.discount_value
      })) : null;
    })()"""),
}


def account_choices() -> dict:
    """Missing sign-in leaves account choices unavailable, while product fields load."""
    result = {'ship_from': None, 'coupons': [], 'default_parcel': '', 'unavailable': []}
    for key, (path, extract) in _READS.items():
        try:
            rows = _session.read(path, extract)
        except AccountConnectionError:
            result['unavailable'].append(key)
            continue
        if key == 'coupons':
            result['coupons'] = rows
        elif rows and isinstance(rows[0], dict):
            if key == 'address':
                result['ship_from'] = str(rows[0].get('ship_from') or '')
            else:
                result['default_parcel'] = str(rows[0].get('default_parcel') or '')
    return result
