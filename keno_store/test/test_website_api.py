import frappe
import unittest

class TestWebsiteAPI(unittest.TestCase):

    def test_get_new_website_items(self):
        # Call the API
        response = frappe.get_doc({
            'doctype': 'Custom DocType',
            'some_field': 'value',
        }).insert()
        
        # Invoke the API function directly
        result = frappe.get_all(
            'Website Item',
            filters={"published": 1},
            fields=[
                "web_item_name", 
                "name", 
                "item_name", 
                "item_code", 
                "website_image", 
                "variant_of", 
                "has_variants", 
                "item_group", 
                "web_long_description", 
                "short_description", 
                "route", 
                "website_warehouse", 
                "ranking", 
                "on_backorder"
            ],
            order_by="creation desc",
            limit=10
        )
        
        self.assertTrue(result, "No items returned")
        self.assertIsInstance(result, dict, "API response is not a dictionary")
        self.assertIn("items", result, "Key 'items' not in response")
        self.assertIsInstance(result["items"], list, "Items is not a list")
        self.assertLessEqual(len(result["items"]), 10, "More than 10 items returned")
        
        # Check the structure of the first item
        if result["items"]:
            item = result["items"][0]
            self.assertIn("web_item_name", item)
            self.assertIn("item_code", item)
            self.assertIn("price_list_rate", item)
            self.assertIn("rating", item)

if __name__ == '__main__':
    unittest.main()
