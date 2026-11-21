"""Chat can see and edit the General form's Measurements (in) fields stored on Conversation.notes."""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from worker_sessions import worker_sessions

from anteroom.database import Base, load_models
from anteroom.repositories.queries import ConversationRepo, ListingRepo
from anteroom.routes.chat import ChatMessage, run_message
from anteroom.services import activity, listing_persistence
from anteroom.services.chat_forms import forms_prompt
from anteroom.services.chat_listing import apply_listing_payload
from anteroom.services.option_lookup import OptionCatalog, chat_with_options


class ChatMeasurementsTest(unittest.TestCase):
    def setUp(self):
        load_models()
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.enterContext(worker_sessions(self.engine))
        self.db = sessionmaker(bind=self.engine)()
        self.conv = ConversationRepo(self.db).create(
            title="Valerie Stevens S Casual Blouse Black Sheer Crinkle",
            notes=json.dumps({"sku": "A12", "sellerNotes": "Lightweight sheer blouse"}),
        )
        self.listing = {
            "title": "Valerie Stevens S Casual Blouse Black Sheer Crinkle",
            "description": "Chic sheer crinkle textured blouse.\n\nMeasurements: See photos",
            "brand": "Valerie Stevens",
            "size": "S",
            "price": 18,
            "condition": "Pre-Owned - Good",
            "department": "Women",
            "category_path": "Women > Tops > Blouses",
        }
        self.rev = listing_persistence.save_revision(self.db, self.conv.id, self.listing, "test")

    def tearDown(self):
        activity.reset()
        self.db.close()
        self.engine.dispose()

    def _general_fields(self, conv_id: str | None = None) -> dict[str, dict]:
        target_id = conv_id or self.conv.id
        with patch("anteroom.services.chat_forms.get_selected_marketplaces", return_value=[]):
            text = asyncio.run(forms_prompt(target_id))
        forms = json.loads(text.split("\n")[-1])
        return {row["path"]: row for row in forms["general"]["fields"]}

    def _notes(self, conv_id: str | None = None) -> dict:
        self.db.expire_all()
        conv = ConversationRepo(self.db).get(conv_id or self.conv.id)
        assert conv is not None
        return json.loads(conv.notes or "{}")

    def test_forms_prompt_exposes_general_measurement_fields_even_when_empty(self):
        fields = self._general_fields()
        self.assertIn("measurements.garment", fields)
        self.assertEqual(fields["measurements.garment"]["section"], "Measurements (in)")
        self.assertEqual(fields["measurements.garment"]["value"], "top")
        self.assertEqual(fields["measurements.garment"]["options"], ["Top", "Pants", "Skirt"])
        self.assertEqual(
            fields["measurements.garment"]["option_values"],
            {"Top": "top", "Pants": "pants", "Skirt": "skirt"},
        )
        for path, label in (
            ("measurements.top.pitToPit", "Pit to Pit"),
            ("measurements.top.length", "Length"),
            ("measurements.top.sleeve", "Sleeve"),
            ("measurements.pants.waist", "Waist"),
            ("measurements.pants.rise", "Rise"),
            ("measurements.pants.inseam", "Inseam"),
            ("measurements.pants.legOpening", "Leg Opening"),
            ("measurements.skirt.waist", "Waist"),
            ("measurements.skirt.hip", "Hip"),
            ("measurements.skirt.length", "Length"),
        ):
            self.assertIn(path, fields)
            self.assertEqual(fields[path]["label"], label)
            self.assertEqual(fields[path]["section"], "Measurements (in)")
            self.assertEqual(fields[path]["input_type"], "number")
            self.assertEqual(fields[path]["value"], "")
        self.assertIn("measurements.other", fields)
        self.assertEqual(fields["measurements.other"]["label"], "Other measurements")
        self.assertEqual(fields["measurements.other"]["value"], "")

    def test_forms_prompt_guesses_garment_and_reads_stored_values(self):
        pants_conv = ConversationRepo(self.db).create(title="Levi's 501 Jeans")
        listing_persistence.save_revision(
            self.db,
            pants_conv.id,
            {**self.listing, "title": "Levi's 501 Jeans", "category_path": "Men > Pants > Jeans"},
            "test",
        )
        self.assertEqual(self._general_fields(pants_conv.id)["measurements.garment"]["value"], "pants")

        ConversationRepo(self.db).write_notes(
            self.conv.id,
            json.dumps({
                "garment": "top",
                "measurements": {"top": {"pitToPit": "16", "length": "19"}},
                "descriptionMeasurements": 'Hem 21"',
            }),
        )
        fields = self._general_fields()
        self.assertEqual(fields["measurements.top.pitToPit"]["value"], "16")
        self.assertEqual(fields["measurements.top.length"]["value"], "19")
        self.assertEqual(fields["measurements.top.sleeve"]["value"], "")
        self.assertEqual(fields["measurements.other"]["value"], 'Hem 21"')

    def test_field_commands_save_measurements_to_notes_without_polluting_listing_json(self):
        reply = 'Added Pit to Pit and Length.\n```json\n' + json.dumps({
            "fields": [
                {"path": "measurements.top.pitToPit", "value": 16},
                {"path": "measurements.top.length", "value": '19"'},
                {"path": "measurements.top.sleeve", "value": "24 1/2 in"},
                {"path": "measurements.other", "value": 'Hem 21"'},
            ],
        }) + "\n```"
        _, saved = apply_listing_payload(self.db, self.conv.id, reply)
        self.assertTrue(saved)
        notes = self._notes()
        self.assertEqual(notes["garment"], "top")
        self.assertEqual(notes["measurements"]["top"], {"pitToPit": "16", "length": "19", "sleeve": "24.5"})
        self.assertEqual(notes["descriptionMeasurements"], 'Hem 21"')
        # Existing note fields are preserved.
        self.assertEqual(notes["sku"], "A12")
        self.assertEqual(notes["sellerNotes"], "Lightweight sheer blouse")
        # Listing JSON is not polluted with a stray measurements key.
        latest = ListingRepo(self.db).get_revisions(self.conv.id)[0].listing_json
        self.assertNotIn("measurements", latest)

    def test_field_commands_save_measurements_and_description_together_and_sync_depop(self):
        reply = 'Updated measurements and description.\n```json\n' + json.dumps({
            "fields": [
                {"path": "measurements.top.pitToPit", "value": "16"},
                {"path": "measurements.top.length", "value": "19"},
                {"path": "description", "value": 'Chic sheer blouse.\n\nMeasurements: Pit to pit: 16"; Length: 19"'},
            ],
        }) + "\n```"
        _, saved = apply_listing_payload(self.db, self.conv.id, reply)
        self.assertTrue(saved)
        notes = self._notes()
        self.assertEqual(notes["garment"], "top")
        self.assertEqual(notes["measurements"]["top"], {"pitToPit": "16", "length": "19"})
        latest = ListingRepo(self.db).get_revisions(self.conv.id)[0].listing_json
        self.assertIn('Pit to pit: 16"', latest["description"])
        self.assertNotIn("measurements", latest)
        self.assertEqual(
            latest["depop_specifics"]["measurements"]["values"],
            [
                {"id": "pit_to_pit", "label": "Pit to pit", "value": "16", "unit": "in"},
                {"id": "length", "label": "Length", "value": "19", "unit": "in"},
            ],
        )

    def test_json_patch_saves_measurements_and_supports_remove_and_mixed_edits(self):
        patch_reply = 'Updated General measurements and description.\n```json\n' + json.dumps([
            {"op": "replace", "path": "/measurements/top/pitToPit", "value": 16},
            {"op": "replace", "path": "/measurements/top/length", "value": "19.5"},
            {"op": "replace", "path": "/description", "value": 'Blouse.\n\nMeasurements: Pit to pit: 16"; Length: 19.5"'},
        ]) + "\n```"
        _, saved = apply_listing_payload(self.db, self.conv.id, patch_reply)
        self.assertTrue(saved)
        notes = self._notes()
        self.assertEqual(notes["garment"], "top")
        self.assertEqual(notes["measurements"]["top"], {"pitToPit": "16", "length": "19.5"})
        latest = ListingRepo(self.db).get_revisions(self.conv.id)[0].listing_json
        self.assertNotIn("measurements", latest)
        self.assertIn("19.5", latest["description"])

        clear_reply = 'Cleared Length.\n```json\n' + json.dumps([
            {"op": "remove", "path": "/measurements/top/length"},
        ]) + "\n```"
        _, saved = apply_listing_payload(self.db, self.conv.id, clear_reply)
        self.assertTrue(saved)
        notes = self._notes()
        self.assertEqual(notes["measurements"]["top"], {"pitToPit": "16", "length": ""})

    def test_legacy_shorts_measurements_preserved_when_updating_pants(self):
        ConversationRepo(self.db).write_notes(
            self.conv.id,
            json.dumps({"garment": "shorts", "measurements": {"shorts": {"waist": "32", "inseam": "7"}}}),
        )
        reply = 'Added rise.\n```json\n' + json.dumps({
            "fields": [{"path": "measurements.pants.rise", "value": "11"}],
        }) + "\n```"
        _, saved = apply_listing_payload(self.db, self.conv.id, reply)
        self.assertTrue(saved)
        notes = self._notes()
        self.assertEqual(notes["garment"], "pants")
        self.assertEqual(notes["measurements"]["pants"], {"waist": "32", "inseam": "7", "rise": "11"})

    def test_field_targets_protocol_and_edit_scope_respect_measurement_paths(self):
        catalog = OptionCatalog({})
        with patch("anteroom.services.chat_forms.get_selected_marketplaces", return_value=[]):
            asyncio.run(forms_prompt(self.conv.id, catalog))

        class Provider:
            def __init__(self):
                self.replies = [
                    '{"_field_targets":["measurements.top.pitToPit","description"]}',
                    'Saved.\n```json\n' + json.dumps({
                        "fields": [
                            {"path": "measurements.top.pitToPit", "value": "16"},
                            {"path": "measurements.top.sleeve", "value": "25"},
                            {"path": "description", "value": 'Updated.\n\nMeasurements: Pit to pit: 16"'},
                        ],
                    }) + "\n```",
                ]

            async def chat(self, messages, stream=True):
                yield self.replies.pop(0)

        declared: list[tuple[str, ...]] = []

        async def run():
            chunks = []
            async for chunk in chat_with_options(
                Provider(),
                [{"role": "system", "content": "sys"}, {"role": "user", "content": "set pit to pit 16"}],
                catalog,
                on_targets=lambda paths: declared.append(tuple(paths)),
            ):
                chunks.append(chunk)
            return "".join(chunks)

        answer = asyncio.run(run())
        self.assertEqual(declared, [("description", "measurements.top.pitToPit")])

        token = activity.edit_fields.set(declared[0])
        try:
            _, saved = apply_listing_payload(self.db, self.conv.id, answer)
        finally:
            activity.edit_fields.reset(token)

        self.assertTrue(saved)
        notes = self._notes()
        self.assertEqual(notes["measurements"]["top"], {"pitToPit": "16"})
        self.assertNotIn("sleeve", notes["measurements"]["top"])
        latest = ListingRepo(self.db).get_revisions(self.conv.id)[0].listing_json
        self.assertIn('Pit to pit: 16"', latest["description"])
        self.assertNotIn("measurements", latest)

    def test_end_to_end_chat_message_sees_and_saves_general_measurements(self):
        seen_prompts: list[str] = []

        class FakeProvider:
            name = "fake"
            listing_model = "fake-model"

            def __init__(self):
                self.replies = [
                    '{"_field_targets":["measurements.top.pitToPit","measurements.top.length"]}',
                    'Added Pit to Pit (16") and Length (19") to General Measurements.\n```json\n' + json.dumps({
                        "fields": [
                            {"path": "measurements.top.pitToPit", "value": "16"},
                            {"path": "measurements.top.length", "value": "19"},
                        ],
                    }) + "\n```",
                ]

            async def chat(self, messages, stream=True):
                if not seen_prompts:
                    seen_prompts.append(messages[0]["content"])
                yield self.replies.pop(0)

        async def send():
            response = await run_message(self.conv.id, ChatMessage(text="Add pit to pit 16 and length 19 to general form"))
            frames = []
            async for chunk in response.body_iterator:
                frames.append(chunk.decode("utf-8") if isinstance(chunk, (bytes, memoryview)) else chunk)
            return "".join(frames)

        with patch("anteroom.routes.chat.get_listing_provider", return_value=FakeProvider()), \
                patch("anteroom.services.chat_forms.get_selected_marketplaces", return_value=[]), \
                patch("anteroom.services.chat_prompts.load_skill_rules", return_value=""):
            stream = asyncio.run(send())

        self.assertIn("event: listing_updated", stream)
        self.assertIn("measurements.top.pitToPit", seen_prompts[0])
        self.assertIn("Measurements (in)", seen_prompts[0])
        notes = self._notes()
        self.assertEqual(notes["garment"], "top")
        self.assertEqual(notes["measurements"]["top"], {"pitToPit": "16", "length": "19"})
        latest = ListingRepo(self.db).get_revisions(self.conv.id)[0].listing_json
        self.assertNotIn("measurements", latest)

    def test_missing_fields_routes_general_measurements_without_shadowing_package_length(self):
        listing_persistence.save_revision(
            self.db,
            self.conv.id,
            {**self.listing, "package_dimensions_in": "10x8x4"},
            "test",
        )
        reply = (
            'Saved values.\n```json\n'
            + json.dumps({
                "missing_fields": [
                    {"marketplace": "General", "field": "Pit to Pit", "value": '16.5"'},
                    {"marketplace": "General", "field": "Other measurements", "value": 'Hem 21"'},
                    {"marketplace": "General", "field": "Length", "value": 12},
                ],
            })
            + "\n```"
        )
        _, saved = apply_listing_payload(self.db, self.conv.id, reply)
        self.assertTrue(saved)
        notes = self._notes()
        self.assertEqual(notes["garment"], "top")
        self.assertEqual(notes["measurements"]["top"], {"pitToPit": "16.5"})
        self.assertEqual(notes["descriptionMeasurements"], 'Hem 21"')
        latest = ListingRepo(self.db).get_revisions(self.conv.id)[0].listing_json
        self.assertEqual(latest["package_dimensions_in"], "12x8x4")

    def test_mixed_field_command_rolls_back_notes_when_listing_save_conflicts(self):
        from anteroom.repositories.queries import RevisionConflict

        reply = 'Update both.\n```json\n' + json.dumps({
            "fields": [
                {"path": "measurements.top.pitToPit", "value": "16"},
                {"path": "description", "value": "Updated description"},
            ],
        }) + "\n```"
        with patch("anteroom.services.listing_editor.save_listing_edit", side_effect=RevisionConflict("stale")):
            _, saved = apply_listing_payload(self.db, self.conv.id, reply)
        self.assertFalse(saved)
        notes = self._notes()
        self.assertNotIn("measurements", notes)
        self.assertNotIn("garment", notes)

