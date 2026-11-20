"""Read Mercari US public shipping quotes with an ephemeral guest session."""
from __future__ import annotations

import contextlib

import httpx
from google.protobuf import descriptor_pb2, descriptor_pool, json_format, message_factory

from anteroom.services import shared_http

BASE_URL = "https://api-double.us.mercari.com"


def _messages():
    # Mercari's mobile wire format, checked against its live responses. The
    # master catalog and the quote response carry different shipping metadata;
    # carrier display names come from the local native catalog by exact ID.
    file = descriptor_pb2.FileDescriptorProto(name="mercari_quotes.proto", package="mercari", syntax="proto3")
    field = descriptor_pb2.FieldDescriptorProto
    integer, string, decimal = field.TYPE_INT32, field.TYPE_STRING, field.TYPE_FLOAT
    definitions = {
        "Token": [(1, "accessToken", string)],
        "Guest": [(2, "token", "Token")],
        "Weight": [(1, "unit", integer), (2, "weight", integer)],
        "Dimensions": [(1, "unit", integer), (2, "length", decimal), (3, "width", decimal), (4, "height", decimal)],
        "Request": [(1, "categoryId", integer), (2, "selectedWeight", "Weight"), (3, "selectedPackageDimension", "Dimensions")],
        "Range": [(1, "min", integer), (2, "max", integer)],
        "Quote": [(1, "id", integer), (4, "fee", integer), (9, "weightRange", "Range"),
                  (12, "etaForSeller", string), (17, "packageSize", decimal), (20, "standardFee", integer),
                  (22, "nonPromotionalFee", integer)],
        "Response": [(1, "shippingClasses", "Quote")],
    }
    for name, fields in definitions.items():
        message = file.message_type.add(name=name)
        for number, key, kind in fields:
            row = message.field.add(name=key, number=number, label=field.LABEL_REPEATED
                                    if name == "Response" else field.LABEL_OPTIONAL)
            if isinstance(kind, str):
                row.type, row.type_name = field.TYPE_MESSAGE, ".mercari." + kind
            else:
                row.type = kind
    pool = descriptor_pool.DescriptorPool()
    pool.Add(file)
    return {name: message_factory.GetMessageClass(pool.FindMessageTypeByName("mercari." + name)) for name in definitions}


MESSAGES = _messages()


async def shipping_quotes(category: int, ounces: int, dimensions: tuple[float, float, float]) -> list[dict]:
    """Read quotes only. No seller login, draft, shipping-label purchase or write."""
    headers = {"Accept": "application/protobuf", "Content-Type": "application/protobuf",
               "User-Agent": "Mercari_d/328532 (iOS 26.0; en-US; iPhone17,3)",
               "X-Platform": "ios", "X-App-Release": "9.37.0", "X-App-Version": "328532"}
    shared = shared_http.current_client()
    client_ctx = (
        contextlib.nullcontext(shared)
        if shared is not None
        else httpx.AsyncClient(base_url=BASE_URL, timeout=20, headers=headers)
    )
    token_url = BASE_URL + "/v1/token" if shared is not None else "/v1/token"
    quotes_url = (
        BASE_URL + "/v1/shipping/available_shipping_classes"
        if shared is not None
        else "/v1/shipping/available_shipping_classes"
    )
    async with client_ctx as client:
        guest = await client.get(token_url, headers=headers, timeout=20)
        guest.raise_for_status()
        token = MESSAGES["Guest"].FromString(guest.content).token.accessToken
        if not token:
            raise ValueError("Mercari did not issue an anonymous session.")
        request = MESSAGES["Request"](
            categoryId=category, selectedWeight={"unit": 1, "weight": ounces},
            selectedPackageDimension=dict(zip(("length", "width", "height"), dimensions, strict=True)) | {"unit": 1},
        )
        # The guest token stays in memory and is never stored or returned.
        response = await client.post(
            quotes_url,
            content=request.SerializeToString(),
            headers={**headers, "Authorization": "Bearer " + token},
            timeout=20,
        )
        response.raise_for_status()
    data = json_format.MessageToDict(MESSAGES["Response"].FromString(response.content),
                                    always_print_fields_with_no_presence=True)
    return data["shippingClasses"]
