import frappe
from frappe import _

def on_pick_list_submit(doc, method):
    """
    Triggered on Pick List submission. Gets the associated Sales Order from Pick List Item
    and checks if all Pick Lists for that Sales Order are submitted.
    Sends a realtime notification if all are submitted.
    """
    
    # Get Sales Order linked to the Pick List
    sales_order = frappe.db.get_value("Pick List Item", {"parent": doc.name}, "sales_order")

    frappe.publish_realtime(
            "liveTrackingUpdates",
            {
                "status": "Packed",
                "message": _("Order is packed."),
                "order_id": sales_order,
            },
            room=sales_order,
        )

    # if not sales_order:
    #     frappe.throw("No Sales Order found for this Pick List.")
    #     return

    # #check order's pick list
    # query = """
    #     SELECT 
    #         pl.name AS pick_list_name, 
    #         pl.status, 
    #         pl.creation, 
    #         pl.owner
    #     FROM 
    #         `tabPick List` pl
    #     INNER JOIN 
    #         `tabPick List Item` pli 
    #     ON 
    #         pli.parent = pl.name
    #     WHERE 
    #         pli.sales_order = %s
    # """
    # pick_lists = frappe.db.sql(query, sales_order, as_dict=True)

    # # Check if all Pick Lists are submitted
    # all_submitted = all(pick_list.status == "Submitted" for pick_list in pick_lists)

    # if all_submitted:
    #     frappe.msgprint(f"Sales order {sales_order} has been packed.")
    #     # Send a realtime notification
    #     frappe.publish_realtime(
    #         "liveTrackingUpdates",
    #         {
    #             "status": "Packed",
    #             "message": _("Order is packed."),
    #             "order_id": sales_order,
    #         },
    #         room=sales_order,
    #     )

