#!/usr/bin/env python3
"""Refresh the public native catalog; never reads an account or creates a listing.

Run from the repository root with Anteroom's Python:
  vendoo-studio/.venv/bin/python vendoo-extension/tools/form-sweep/mercari/capture_catalog.py

A temporary anonymous credential stays in memory. Only public category, size,
brand and carrier-label facts are written. Field layout stays in the native
form definition; prices always come from live shipping quotes.
"""
import json
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from google.protobuf import (
    descriptor_pb2,
    descriptor_pool,
    json_format,
    message_factory,
)

ROOT = Path(__file__).resolve().parents[4]
OUTPUT = ROOT / 'skills/list-this/references/mercari-native-catalog.json'
BASE = 'https://api-double.us.mercari.com'
F = descriptor_pb2.FieldDescriptorProto
I, S = F.TYPE_INT32, F.TYPE_STRING
schema = descriptor_pb2.FileDescriptorProto(name='mercari_catalog.proto', package='mercari', syntax='proto3')


def message(name, fields):
    definition = schema.message_type.add(name=name)
    for number, key, kind, repeated in fields:
        field = definition.field.add(number=number, name=key, label=F.LABEL_REPEATED if repeated else F.LABEL_OPTIONAL)
        if isinstance(kind, str):
            field.type, field.type_name = F.TYPE_MESSAGE, '.mercari.' + kind
        else:
            field.type = kind


message('Token', [(1, 'accessToken', S, False)])
message('Guest', [(2, 'token', 'Token', False)])
message('Category', [(1, 'id', I, False), (2, 'name', S, False), (3, 'parentId', I, False),
                     (5, 'itemSizeGroupId', I, False), (9, 'displayOrder', I, False)])
message('Size', [(1, 'id', I, False), (2, 'name', S, False), (4, 'title', S, False), (5, 'subtitle', S, False)])
message('SubGroup', [(1, 'id', I, False), (2, 'title', S, False), (3, 'itemSizes', 'Size', True)])
message('Group', [(1, 'id', I, False), (2, 'sizeIds', I, True), (3, 'name', S, False),
                  (5, 'category', S, False), (6, 'subGroups', 'SubGroup', True), (7, 'title', S, False)])
message('Brand', [(1, 'id', I, False), (2, 'name', S, False)])
message('Label', [(1, 'id', I, False), (2, 'name', S, False), (5, 'carrier', S, False), (6, 'carrierDisplayName', S, False)])
# Verified field tags from Mercari US /v1/master, rather than the older public
# protocol's MasterSet tag numbers. Its item messages match the sell page.
message('Master', [(10, 'itemCategories', 'Category', True), (12, 'itemSizes', 'Size', True),
                   (13, 'itemSizeGroups', 'Group', True), (14, 'itemBrands', 'Brand', True), (23, 'shippingLabels', 'Label', True)])
message('Response', [(1, 'master', 'Master', False)])
pool = descriptor_pool.DescriptorPool()
pool.Add(schema)


def parse(name, data):
    cls = message_factory.GetMessageClass(pool.FindMessageTypeByName('mercari.' + name))
    return cls.FromString(data)


def main():
    headers = {'Accept': 'application/protobuf', 'User-Agent': 'Mercari_d/328532 (iOS 26.0; en-US; iPhone17,3)',
               'X-Platform': 'ios', 'X-App-Release': '9.37.0', 'X-App-Version': '328532'}

    def get(path):
        with urllib.request.urlopen(urllib.request.Request(BASE + path, headers=headers), timeout=30) as response:
            return response.read()

    token = parse('Guest', get('/v1/token')).token.accessToken
    if not token:
        raise ValueError('Mercari did not issue an anonymous guest session.')
    headers['Authorization'] = 'Bearer ' + token
    master = json_format.MessageToDict(parse('Response', get('/v1/master')),
                                      always_print_fields_with_no_presence=True)['master']
    assert master['itemCategories'] and master['itemBrands'] and master['itemSizeGroups']
    sizes = {row['id']: row for row in master['itemSizes']}
    subgroups = []
    for group in master['itemSizeGroups']:
        for index, size_id in enumerate(group['sizeIds']):
            assert size_id in sizes
            sizes[size_id].update(itemSizeGroupId=group['id'], displayOrder=index)
        children = group.pop('subGroups')
        group['subGroupIds'] = [row['id'] for row in children]
        for child in children:
            child_sizes = child.pop('itemSizes')
            child['sizeIds'] = [row['id'] for row in child_sizes]
            subgroups.append(child)
            for size in child_sizes:
                assert all(sizes[size['id']][key] == value for key, value in size.items())
                sizes[size['id']]['itemSizeSubGroupId'] = child['id']
    for size in sizes.values():
        size.setdefault('itemSizeSubGroupId', 0)
    master['itemSizeSubGroups'] = subgroups
    data = json.loads(OUTPUT.read_text())
    data.update(master)
    data['provenance'].update(captured=str(datetime.now(UTC).date()), endpoint=BASE + '/v1/master')
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: len(value) for key, value in master.items()}))


if __name__ == '__main__':
    main()
