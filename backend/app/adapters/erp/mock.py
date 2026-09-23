from __future__ import annotations

from typing import Any


class MockERP:
    """Fixture-facing ERP simulator. Every returned fact is labeled as mock."""

    def sales_order_release(self, order_id: str, quantity: int) -> dict[str, Any]:
        return {"object_id": order_id, "status": "RELEASED", "quantity": quantity, "authority": "MockERP"}

    def material_demand(self, demand_id: str, material_id: str, quantity: str) -> dict[str, Any]:
        return {
            "object_id": demand_id,
            "material_id": material_id,
            "required_quantity": quantity,
            "authority": "MockERP",
        }

    def purchase_order_draft(self, order_id: str, selected_option: dict[str, Any]) -> dict[str, Any]:
        return {"object_id": order_id, "status": "DRAFT", "selected_option": selected_option, "authority": "MockERP"}

    def delivered(self, delivery_id: str, shipment_event_id: str) -> dict[str, Any]:
        return {
            "object_id": delivery_id,
            "status": "DELIVERED",
            "source_shipment_event": shipment_event_id,
            "authority": "MockERP",
        }
