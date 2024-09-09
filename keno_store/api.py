import hashlib
import uuid
import frappe
from frappe import _
from frappe.auth import CookieManager
from frappe.utils import cint
from frappe.utils import flt
import frappe.utils
from webshop.webshop.product_data_engine.filters import ProductFiltersBuilder
from webshop.webshop.product_data_engine.query import ProductQuery
from webshop.webshop.doctype.override_doctype.item_group import get_child_groups_for_website
from webshop.webshop.utils.product import get_non_stock_item_status
from webshop.webshop.shopping_cart.product_info import get_product_info_for_website
frappe.utils.logger.set_log_level("DEBUG")
logger = frappe.logger("api", allow_site=True, file_count=50)

@frappe.whitelist(allow_guest=True)
def get_zone_by_zip(zip_code):
    try:
        if not zip_code:
            frappe.throw(_("Zip code is required"), frappe.exceptions.ValidationError)

        # Fetch the Zone Name where the zip code is found in the comma-separated list
        zone = frappe.db.sql("""
            SELECT 
                `zone_name` 
            FROM 
                `tabDelivery Zone` 
            WHERE 
                FIND_IN_SET(%s, `zip_codes`) > 0
            """, (zip_code), as_dict=True)
        
        if not zone:
            frappe.throw(_("No delivery zone found for the provided zip code: {0}").format(zip_code), frappe.exceptions.DoesNotExistError)
        
        return {"zone": zone[0].zone_name}

    except frappe.exceptions.ValidationError as e:
        frappe.log_error(message=str(e), title="Validation Error in get_zone_by_zip")
        return {"error": str(e)}

    except frappe.exceptions.DoesNotExistError as e:
        frappe.log_error(message=str(e), title="Zone Not Found")
        return {"error": str(e)}

    except Exception as e:
        frappe.log_error(message=str(e), title="Unexpected Error in get_zone_by_zip")
        return {"error": "An unexpected error occurred. Please try again later."}

@frappe.whitelist(allow_guest=True)
def signup_customer(full_name, email, mobile=None, password=None, confirm_password=None):
    try:
        # Check if passwords match
        if password != confirm_password:
            raise ValueError(_("Passwords do not match"))

        # Check if user already exists
        if frappe.db.exists("User", email):
            raise ValueError(_("User with this email {0}, already exists").format(email))

        # Check if mobile number is unique
        if mobile:
            if frappe.db.exists("Customer", {"mobile_no": mobile}):
                raise ValueError(_("A customer with this mobile number already exists"))

        # Start a database transaction
        frappe.db.begin()

        # Create the User
        user = frappe.get_doc({
            "doctype": "User",
            "email": email,
            "mobile_no": mobile,
            "first_name": full_name.split()[0],  # Assumes first name is the first part of full name
            "last_name": " ".join(full_name.split()[1:]),  # Assumes last name is the rest
            "enabled": 1,
            "user_type": "Website User",
            # "roles": [
            #     {"role": "Customer"}  # Add Customer role to the user
            # ],
            "role_profile_name": "Customer",
            "new_password": password,  # Set the user's password
            "send_welcome_email": 0  # Avoid sending the welcome email automatically
        })

        user.insert(ignore_permissions=True)

        # Create the Customer
        customer = frappe.get_doc({
            "doctype": "Customer",
            "customer_name": full_name,
            "customer_type": "Individual",
            "customer_group": "All Customer Groups",
            "territory": "All Territories",
            "email_id": email,
            "mobile_no": mobile,
            "portal_users": [{"user": user.name}]  # Ensure we use `user.name` here
        })
        customer.insert(ignore_permissions=True)

        # Commit the transaction
        frappe.db.commit()

        # Optionally send a welcome email
        send_welcome_email(email, full_name)  # Commented out to skip welcome email

        return {"status": "success", "message": _("User and Customer created successfully")}

    except ValueError as ve:
        # frappe.log_error(message=str(ve), title="Signup Validation Error")
        frappe.db.rollback()
        return {"status": "error", "message": str(ve)}

    except Exception as e:
        frappe.log_error(message=str(e), title="Signup Customer Error")
        frappe.db.rollback()
        return {"status": "error", "message": _("An error occurred while creating the user and customer")}


def send_welcome_email(email, full_name):
    try:
        # Define the template path
        template_path = 'keno_store/templates/emails/welcome_email.html'
        
        # Attempt to get and render the template
        try:
            template = frappe.get_template(template_path)
        except IOError:
            error_message = _("Email template not found at path: {0}").format(template_path)
            frappe.log_error(message=error_message, title="Email Template Error")
            return {"status": "error", "message": error_message}
        
        # Render the email content with context
        message = template.render({
            'customer_name': full_name
        })
        
        # Define email subject
        subject = _("Welcome to Keno Store!")
        
        # Send the email
        frappe.sendmail(
            recipients=[email],
            subject=subject,
            message=message,
            delayed=False,
            retry=3
        )
        
        return {"status": "success", "message": _("Welcome email sent successfully to {0}").format(email)}
    
    except frappe.OutgoingEmailError as e:
        error_message = _("Failed to send welcome email to {0}. SMTP server error: {1}").format(email, str(e))
        frappe.log_error(message=error_message, title="SMTP Error")
        return {"status": "error", "message": error_message}
    
    except Exception as e:
        error_message = _("An unexpected error occurred while sending welcome email to {0}: {1}").format(email, str(e))
        frappe.log_error(message=error_message, title="Send Welcome Email Error")
        return {"status": "error", "message": error_message}


@frappe.whitelist(allow_guest=True)
def get_weekly_schedule(zip_code=None):
    try:
        # Initialize a dictionary to hold the schedule for all zones
        all_zone_schedules = {}

        # If a zip code is provided, find the specific zone
        if zip_code:
            zones = frappe.get_all(
                "Delivery Zone",
                filters={"zip_codes": ["like", f"%{zip_code}%"]},
                fields=["zone_name"],
                limit_page_length=1  # Ensure only one zone is fetched
            )

            if not zones:
                frappe.throw(_("No zone found for the provided zip code: {0}").format(zip_code), frappe.exceptions.DoesNotExistError)
        else:
            # If no zip code is provided, get all zones
            zones = frappe.get_all(
                "Delivery Zone",
                fields=["zone_name"]
            )

            if not zones:
                frappe.throw(_("No zones found in the system"), frappe.exceptions.DoesNotExistError)

        # Weekdays and corresponding field names for child tables
        weekdays = {
            "Monday": "monday_slots",
            "Tuesday": "tuesday_slots",
            "Wednesday": "wednesday_slots",
            "Thursday": "thursday_slots",
            "Friday": "friday_slots",
            "Saturday": "saturday_slots",
            "Sunday": "sunday_slots"
        }

        # Iterate over each zone and fetch the weekly schedule
        for zone in zones:
            zone_name = zone.zone_name

            # Fetch the Delivery Zone Schedule for the current zone
            try:
                schedule_doc = frappe.get_doc("Delivery Zone Schedule", {"delivery_zone": zone_name})

                # Initialize a dictionary to hold the weekly schedule for the current zone
                weekly_schedule = {}

                # Iterate through each weekday and fetch the delivery slots
                for day, field_name in weekdays.items():
                    delivery_slots = [
                        {"start_time": str(slot.start_time), "end_time": str(slot.end_time)}
                        for slot in getattr(schedule_doc, field_name, [])
                    ]

                    # Only add the day to the schedule if there are slots available
                    if delivery_slots:
                        weekly_schedule[day] = delivery_slots

                # Add the zone's weekly schedule to the overall schedules dictionary
                if weekly_schedule:
                    all_zone_schedules[zone_name] = {"weekly_schedule": weekly_schedule}

            except frappe.DoesNotExistError:
                frappe.log_error(message=f"Schedule not found for zone: {zone_name}", title="Schedule Not Found")
                continue

        # Return the appropriate result
        if zip_code:
            return {"zones": {zones[0].zone_name: all_zone_schedules.get(zones[0].zone_name)}}
        else:
            return {"zones": all_zone_schedules}

    except frappe.exceptions.ValidationError as e:
        frappe.log_error(message=str(e), title="Validation Error in get_weekly_schedule_by_zip")
        return {"error": str(e)}

    except frappe.exceptions.DoesNotExistError as e:
        frappe.log_error(message=str(e), title="Zone or Schedule Not Found")
        return {"error": str(e)}

    except Exception as e:
        frappe.log_error(message=str(e), title="Unexpected Error in get_weekly_schedule_by_zip")
        return {"error": "An unexpected error occurred. Please try again later."}



@frappe.whitelist(allow_guest=True)
def get_product_filter_data(query_args=None):
    """
    Returns filtered products and discount filters.

    Args:
        query_args (dict): contains filters to get products list
	   Query Args filters:
        search (str): Search Term.
        field_filters (dict): Keys include item_group, brand, etc.
        attribute_filters(dict): Keys include Color, Size, etc.
        start (int): Offset items by
        item_group (str): Valid Item Group
        from_filters (bool): Set as True to jump to page 1
    """
    logger.debug(query_args)
    if isinstance(query_args, str):
        query_args = json.loads(query_args)

    
    query_args = frappe._dict(query_args)

    if query_args:
        search = query_args.get("search")
        field_filters = query_args.get("field_filters", {})
        attribute_filters = query_args.get("attribute_filters", {})
        start = cint(query_args.start) if query_args.get("start") else 0
        item_group = query_args.get("item_group")
        from_filters = query_args.get("from_filters")
        logger.debug(field_filters)
    else:
        search, attribute_filters, item_group, from_filters = None, None, None, None
        field_filters = {}
        start = 0

    # if new filter is checked, reset start to show filtered items from page 1
    if from_filters:
        start = 0

    sub_categories = []
    if item_group:
        sub_categories = get_child_groups_for_website(item_group, immediate=True)

    engine = ProductQuery()

    try:
        result = engine.query(
            attribute_filters,
            field_filters,
            search_term=search,
            start=start,
            item_group=item_group,
        )
        logger.debug(result)
    except Exception:
        frappe.log_error("Product query with filter failed")
        return {"exc": "Something went wrong!"}

    # discount filter data
    filters = {}
    discounts = result["discounts"]

    if discounts:
        filter_engine = ProductFiltersBuilder()
        filters["discount_filters"] = filter_engine.get_discount_filters(discounts)
        logger.debug(filters["discount_filters"])
    
    # Adding ratings to each product
    for item in result["items"]:
        item_code = item.get("item_code")
        if item_code:
            ratings = frappe.db.get_all("Item Review", filters={"item": item_code}, fields=["rating"])
            if ratings:
                total_rating = sum([r["rating"] for r in ratings])
                average_rating = total_rating / len(ratings)
                item["rating"] = round(average_rating, 1)  # Round to 2 decimal places
            else:
                item["rating"] = 0  # Default if no ratings available


    return {
        "items": result["items"] or [],
        "filters": filters,
        "settings": engine.settings,
        "sub_categories": sub_categories,
        "items_count": result["items_count"],
    }


@frappe.whitelist(allow_guest=True)
def get_website_item_details(item_code):
    try:
        # Fetch the website item document
        website_item = frappe.get_doc("Website Item", {"item_code": item_code})
        logger.debug(website_item)

        if not website_item:
            frappe.throw(_("Website Item not found"))

        # Basic website item details
        item_details = {
            "item_code": website_item.item_code,
            "item_name": website_item.item_name,
            "description": website_item.description,
            # "image": website_item.website_image,
            "web_long_description": website_item.web_long_description,
            "is_in_stock": frappe.db.get_value("Bin", {"item_code": item_code}, "actual_qty") > 0,
            "uom": website_item.stock_uom,
        }
        # get_stock_availability(item_details, website_item.get("website_warehouse"));
        

        # Get stock quantity
        stock_qty = frappe.db.get_value("Bin", {"item_code": item_code}, "projected_qty ")
        item_details["stock_qty"] = stock_qty if stock_qty else 0

        try:
            # Fetch product information including pricing details
            product_info = get_product_info_for_website(item_code, skip_quotation_creation=True).get(
                "product_info"
            )
            if product_info and product_info["price"]:
                item_details.update({
                    "formatted_mrp": product_info["price"].get("formatted_mrp"),
                    "formatted_price": product_info["price"].get("formatted_price"),
                    "price_list_rate": product_info["price"].get("price_list_rate")
                })
            if product_info["price"].get("discount_percent"):
                item_details.update({
                    "discount_percent" : flt(product_info["price"].discount_percent)
                })
            if item_details.formatted_mrp:
                item_details.update({
                    "discount" : product_info["price"].get("formatted_discount_percent") or product_info["price"].get(
                        "formatted_discount_rate"
                    )
                })
        except Exception as e:
            frappe.log_error(message=f"Error fetching product info for item {item_code}: {str(e)}", 
                                title="Get New Website Items Error")
            # You may also choose to skip this item or return a default value instead
            

        # Get item price from Item Price doctype
        item_price = frappe.db.get_value("Item Price", {"item_code": item_code, "selling": 1}, ["price_list_rate", "currency"], as_dict=True)
        if item_price:
            item_details.update({
                "price": item_price.price_list_rate,
                "currency": item_price.currency
            })

        # Get item reviews from the custom Website Item Review doctype
        reviews = frappe.get_all("Item Review", filters={"item": item_code},
                                 fields=["customer", "rating", "review_title", "comment", "published_on"], order_by="published_on desc")

        item_details.update({
            "reviews": reviews,
            "average_rating": round(sum([r['rating'] for r in reviews]) / len(reviews),1) if reviews else 0
        })

        # Get multiple images from Website Slideshow Item
        logger.debug(website_item.slideshow)
        image_list = []
        slideshow_name = website_item.slideshow
        
        if slideshow_name:
            images = frappe.get_all("Website Slideshow Item", filters={"parent": slideshow_name}, fields=["image"], order_by="idx asc")
            image_list = [{"image": img.image} for img in images]
        else : 
            image_list = [{"image": website_item.website_image}]
        item_details["image_list"] = image_list

        # Fetch website specifications
        specifications = frappe.get_all("Item Website Specification", filters={"parent": website_item.name}, 
                                        fields=["label", "description"], order_by="idx asc")
        item_details["specifications"] = [{"label": spec.label, "value": spec.value} for spec in specifications]

        logger.debug(item_details)

        return item_details

    except frappe.DoesNotExistError:
        frappe.log_error(f"Website Item with code {item_code} does not exist.", "Item Not Found Error")
        return {"error": f"Website Item with code {item_code} does not exist."}, 404

    except frappe.ValidationError as e:
        frappe.log_error(str(e), "Validation Error")
        return {"error": str(e)}, 400

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Unexpected Error in get_website_item_details API")
        return {"error": "An unexpected error occurred. Please try again later."}, 500


def get_stock_availability(item):
    """Modify item object and add stock details."""
    from webshop.templates.pages.wishlist import (
        get_stock_availability as get_stock_availability_from_template,
	)
    # item.update({
    #     "is_stock_item": False
    # })

    item.is_in_stock = get_stock_availability_from_template(item.item_code, item.warehouse)
    # # warehouse = item_details.get("website_warehouse")
    # is_stock_item = frappe.get_cached_value("Item", item_details.item_code, "is_stock_item")
    # logger.debug(is_stock_item)

    # # if item_details.get("on_backorder"):
    # #     return

    # if not is_stock_item:
    #     if warehouse:
    #         # product bundle case
    #         item_details.is_in_stock = get_non_stock_item_status(item_details.item_code, "website_warehouse")
    #     else:
    #         item_details.is_in_stock = True
    # elif warehouse:
    #     # stock item and has warehouse
    #     item_details.is_in_stock = get_stock_availability_from_template(item_details.item_code, warehouse)

@frappe.whitelist(allow_guest=True)
def search(query):
    from webshop.templates.pages.product_search import (
        product_search as product_search_from_template,
        get_category_suggestions as get_category_suggestions_from_template
    )
    product_results = product_search_from_template(query)
    category_results = get_category_suggestions_from_template(query)
    
    return {
		"product_results": product_results.get("results") or [],
		"category_results": category_results.get("results") or [],
	}

@frappe.whitelist(allow_guest=True)
def get_new_website_items(limit=10, price_list="Standard Selling"):
    """
    Fetch the latest website items based on creation date.

    Args:
        limit (int): The number of items to return (default is 10).

    Returns:
        dict: A dictionary containing the list of new website items.
    """
    try:
        # Fetch the latest items from the Website Item doctype
        items = frappe.get_all(
            "Website Item",
            filters={"published": 1},  # Ensure only published items are fetched
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
            order_by="creation desc",  # Order by creation date to get the newest items
            limit=limit  # Limit the number of items returned
        )
        
        for item in items:
            try:
                # Fetch product information including pricing details
                product_info = get_product_info_for_website(item.item_code, skip_quotation_creation=True).get(
                    "product_info"
                )
                if product_info and product_info["price"]:
                    item.update({
                        "formatted_mrp": product_info["price"].get("formatted_mrp"),
                        "formatted_price": product_info["price"].get("formatted_price"),
                        "price_list_rate": product_info["price"].get("price_list_rate")
                    })
                if product_info["price"].get("discount_percent"):
                    item.update({
                        "discount_percent" : flt(product_info["price"].discount_percent)
                    })
                if item.formatted_mrp:
                    item.update({
                        "discount" : product_info["price"].get("formatted_discount_percent") or product_info["price"].get(
                            "formatted_discount_rate"
                        )
                    })
            except Exception as e:
                frappe.log_error(message=f"Error fetching product info for item {item.get('item_code')}: {str(e)}", 
                                 title="Get New Website Items Error")
                # You may also choose to skip this item or return a default value instead
                continue

            try:
                # Fetch item rating
                ratings = frappe.get_all("Item Review", filters={"item": item.item_code}, fields=["rating"])
                if ratings:
                    total_rating = sum([r["rating"] for r in ratings])
                    average_rating = total_rating / len(ratings)
                    item["rating"] = round(average_rating, 1)
                else:
                    item["rating"] = 0
            except Exception as e:
                frappe.log_error(message=f"Error fetching ratings for item {item.get('item_code')}: {str(e)}", 
                                 title="Get New Website Items Error")
                item["rating"] = 0  # Default value if rating fetch fails

    except Exception as e:
        frappe.log_error(message=f"Error fetching website items: {str(e)}", title="Get New Website Items Error")
        return {"error": "An error occurred while fetching new website items."}

    return {"items": items}


@frappe.whitelist(allow_guest=True)
def get_hot_deals_website_items(limit=10, price_list="Standard Selling"):
    """
    Fetch website items that have active pricing rules ("Hot Deals").

    Args:
        limit (int): The number of items to return (default is 10).
        price_list (str): The price list to fetch item prices from (default is "Standard Selling").

    Returns:
        dict: A dictionary containing the list of hot deal website items or an error message.
    """
    try:
        # Fetch active pricing rules
        active_pricing_rules = frappe.get_all(
            "Pricing Rule",
            filters={
                "disable": 0,
                "apply_on": "Item Code",
                "valid_from": ["<=", frappe.utils.nowdate()],
                "valid_upto": ["in", ["", None, ["gt", frappe.utils.nowdate()]]],
                "discount_percentage": [">", 0]
            },
            fields=["name"]
        )

        # If no active pricing rules are found, return an empty list
        if not active_pricing_rules:
            return {"items": []}

        # Fetch items linked to these pricing rules
        pricing_rule_names = [rule["name"] for rule in active_pricing_rules]
        items = frappe.get_all(
            "Pricing Rule Item Code",
            filters={"parent": ["in", pricing_rule_names]},
            fields=["item_code"]
        )
        logger.debug(items)

        item_codes = [item["item_code"] for item in items]

        # Step 2: Fetch Website Items linked to these item codes
        items = frappe.get_all(
            "Website Item",
            filters={
                "item_code": ["in", item_codes],
                "published": 1  # Ensure only published items are fetched
            },
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
            order_by="creation desc",  # Order by creation date to get the newest items
            limit=limit
        )

        # Step 3: Enhance each item with pricing and rating details
        for item in items:
            try:
                # Fetch product information including pricing details
                product_info = get_product_info_for_website(item.item_code, skip_quotation_creation=True).get(
                    "product_info"
                )
                if product_info and product_info["price"]:
                    item.update({
                        "formatted_mrp": product_info["price"].get("formatted_mrp"),
                        "formatted_price": product_info["price"].get("formatted_price"),
                        "price_list_rate": product_info["price"].get("price_list_rate")
                    })
                if product_info["price"].get("discount_percent"):
                    item.update({
                        "discount_percent" : flt(product_info["price"].discount_percent)
                    })
                if item.formatted_mrp:
                    item.update({
                        "discount" : product_info["price"].get("formatted_discount_percent") or product_info["price"].get(
                            "formatted_discount_rate"
                        )
                    })
            except Exception as e:
                frappe.log_error(message=f"Error fetching product info for item {item.get('item_code')}: {str(e)}", 
                                 title="Get New Website Items Error")
                # You may also choose to skip this item or return a default value instead
                continue

            try:
                # Fetch item rating
                ratings = frappe.get_all("Item Review", filters={"item": item.item_code}, fields=["rating"])
                if ratings:
                    total_rating = sum([r["rating"] for r in ratings])
                    average_rating = total_rating / len(ratings)
                    item["rating"] = round(average_rating, 1)
                else:
                    item["rating"] = 0
            except Exception as e:
                frappe.log_error(message=f"Error fetching ratings for item {item.get('item_code')}: {str(e)}", 
                                 title="Get New Website Items Error")
                item["rating"] = 0  # Default value if rating fetch fails

        return {"items": items}

    except Exception as e:
        frappe.log_error(f"Failed to get hot deals: {str(e)}")
        return {"exc": "Something went wrong!"}


@frappe.whitelist(allow_guest=True)
def get_top_selling_products(limit=10, period="last_month"):
    """
    Fetch the top-selling products based on the number of items sold.

    Args:
        limit (int): The number of top-selling items to return (default is 10).
        period (str): The period to consider for sales (default is "last_month").

    Returns:
        dict: A dictionary containing the list of top-selling products.
    """
    try:
        # Define the period for fetching sales data
        if period == "last_month":
            start_date = frappe.utils.add_months(frappe.utils.nowdate(), -1)
        elif period == "last_week":
            start_date = frappe.utils.add_days(frappe.utils.nowdate(), -7)
        else:
            start_date = None  # Use all available data if no specific period is defined

        filters = {}
        if start_date:
            filters["creation"] = [">=", start_date]

        # Fetch top-selling items based on quantity sold
        top_items = frappe.db.get_all(
            "Sales Invoice Item",
            filters=filters,
            fields=["item_code", "sum(qty) as total_sold"],
            group_by="item_code",
            order_by="total_sold desc",
            limit=limit
        )

        item_codes = [item["item_code"] for item in top_items]
        logger.debug(item_codes)

        # Fetch website items corresponding to the top-selling item codes
        top_selling_items = frappe.get_all(
            "Website Item",
            filters={"item_code": ["in", item_codes], "published": 1},
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
            ]
        )

        for item in top_selling_items:
            # Fetch pricing information
            product_info = get_product_info_for_website(item.item_code, skip_quotation_creation=True).get(
                "product_info"
            )
            if product_info and product_info["price"]:
                item.update({
                    "formatted_mrp": product_info["price"].get("formatted_mrp"),
                    "formatted_price": product_info["price"].get("formatted_price"),
                    "price_list_rate": product_info["price"].get("price_list_rate"),
                    "discount_percent": flt(product_info["price"].get("discount_percent", 0)),
                    "discount": product_info["price"].get("formatted_discount_percent") or product_info["price"].get(
                        "formatted_discount_rate"
                    ),
                })

            # Fetch item rating
            ratings = frappe.get_all("Item Review", filters={"item": item.item_code}, fields=["rating"])
            if ratings:
                total_rating = sum([r["rating"] for r in ratings])
                average_rating = total_rating / len(ratings)
                item["rating"] = round(average_rating, 1)
            else:
                item["rating"] = 0

        return {"items": top_selling_items}

    except Exception as e:
        frappe.log_error(f"Failed to fetch top-selling products: {str(e)}", "Top Selling Products API Error")
        return {"exc": "Something went wrong!"}


@frappe.whitelist(allow_guest=True)
def get_limited_time_offers(limit=10, price_list="Standard Selling", days=7):
    """
    Fetch hot deals (items with active pricing rules) that will expire within the next X days.

    Args:
        limit (int): The number of items to return (default is 10).
        price_list (str): The price list to fetch item prices from (default is "Standard Selling").

    Returns:
        dict: A dictionary containing the list of hot deal website items or an error message.
    """
    today_date = frappe.utils.nowdate()
    expiring_soon_date = frappe.utils.add_days(today_date,7)
    try:
        # Fetch active pricing rules
        active_pricing_rules = frappe.get_all(
            "Pricing Rule",
            filters={
                "disable": 0,
                "apply_on": "Item Code",
                "valid_from": ["<=", today_date],
                "valid_upto": ["between", [today_date, expiring_soon_date]],
                "discount_percentage": [">", 0]
            },
            fields=["name"]
        )

        # If no active pricing rules are found, return an empty list
        if not active_pricing_rules:
            return {"items": []}

        # Fetch items linked to these pricing rules
        pricing_rule_names = [rule["name"] for rule in active_pricing_rules]
        items = frappe.get_all(
            "Pricing Rule Item Code",
            filters={"parent": ["in", pricing_rule_names]},
            fields=["item_code"]
        )
        logger.debug(items)

        item_codes = [item["item_code"] for item in items]

        # Step 2: Fetch Website Items linked to these item codes
        items = frappe.get_all(
            "Website Item",
            filters={
                "item_code": ["in", item_codes],
                "published": 1  # Ensure only published items are fetched
            },
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
            order_by="creation desc",  # Order by creation date to get the newest items
            limit=limit
        )

        # Step 3: Enhance each item with pricing and rating details
        for item in items:
            try:
                # Fetch product information including pricing details
                product_info = get_product_info_for_website(item.item_code, skip_quotation_creation=True).get(
                    "product_info"
                )
                if product_info and product_info["price"]:
                    item.update({
                        "formatted_mrp": product_info["price"].get("formatted_mrp"),
                        "formatted_price": product_info["price"].get("formatted_price"),
                        "price_list_rate": product_info["price"].get("price_list_rate")
                    })
                if product_info["price"].get("discount_percent"):
                    item.update({
                        "discount_percent" : flt(product_info["price"].discount_percent)
                    })
                if item.formatted_mrp:
                    item.update({
                        "discount" : product_info["price"].get("formatted_discount_percent") or product_info["price"].get(
                            "formatted_discount_rate"
                        )
                    })
            except Exception as e:
                frappe.log_error(message=f"Error fetching product info for item {item.get('item_code')}: {str(e)}", 
                                 title="Get New Website Items Error")
                # You may also choose to skip this item or return a default value instead
                continue

            try:
                # Fetch item rating
                ratings = frappe.get_all("Item Review", filters={"item": item.item_code}, fields=["rating"])
                if ratings:
                    total_rating = sum([r["rating"] for r in ratings])
                    average_rating = total_rating / len(ratings)
                    item["rating"] = round(average_rating, 1)
                else:
                    item["rating"] = 0
            except Exception as e:
                frappe.log_error(message=f"Error fetching ratings for item {item.get('item_code')}: {str(e)}", 
                                 title="Get New Website Items Error")
                item["rating"] = 0  # Default value if rating fetch fails

        return {"items": items}

    except Exception as e:
        frappe.log_error(f"Failed to get hot deals: {str(e)}")
        return {"exc": "Something went wrong!"}
    

@frappe.whitelist(allow_guest=True)
def get_special_discount_items(limit=10):
    """
    Fetch special discount (items with active offer named "special discount").

    Args:
        limit (int): The number of items to return (default is 10).

    Returns:
        dict: A dictionary containing the list of special discount website items or an error message.
    """
    # Fetch Website Offers with the offer_title "Special Discount"
    try:
        # Fetch web item codes by "Special Discount" offer title
        parents = frappe.get_all(
            "Website Offer",
            filters={"offer_title": "Special Discount"},
            fields=["parent"],
            limit=limit
        )
        if not parents:
            return {"items": []}
        
        web_items = ','.join([parent['parent'] for parent in parents])

        # Fetch Website Items linked to these web_items
        items = frappe.get_all(
            "Website Item",
            filters={
                "name": ["in", web_items],
                "published": 1  # Ensure only published items are fetched
            },
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
            order_by="creation desc",  # Order by creation date to get the newest items
            limit=limit
        )

        logger.debug("get_special_discount_items")
        logger.debug(items)

        # Step 3: Enhance each item with pricing and rating details
        for item in items:
            try:
                # Fetch product information including pricing details
                product_info = get_product_info_for_website(item.item_code, skip_quotation_creation=True).get(
                    "product_info"
                )
                if product_info and product_info["price"]:
                    item.update({
                        "formatted_mrp": product_info["price"].get("formatted_mrp"),
                        "formatted_price": product_info["price"].get("formatted_price"),
                        "price_list_rate": product_info["price"].get("price_list_rate")
                    })
                if product_info["price"].get("discount_percent"):
                    item.update({
                        "discount_percent" : flt(product_info["price"].discount_percent)
                    })
                if item.formatted_mrp:
                    item.update({
                        "discount" : product_info["price"].get("formatted_discount_percent") or product_info["price"].get(
                            "formatted_discount_rate"
                        )
                    })
            except Exception as e:
                frappe.log_error(message=f"Error fetching product info for item {item.get('item_code')}: {str(e)}", 
                                 title="Get New Website Items Error")
                # You may also choose to skip this item or return a default value instead
                continue

            try:
                # Fetch item rating
                ratings = frappe.get_all("Item Review", filters={"item": item.item_code}, fields=["rating"])
                if ratings:
                    total_rating = sum([r["rating"] for r in ratings])
                    average_rating = total_rating / len(ratings)
                    item["rating"] = round(average_rating, 1)
                else:
                    item["rating"] = 0
            except Exception as e:
                frappe.log_error(message=f"Error fetching ratings for item {item.get('item_code')}: {str(e)}", 
                                 title="Get New Website Items Error")
                item["rating"] = 0  # Default value if rating fetch fails

        return {"items": items}

    except Exception as e:
        frappe.log_error(f"Failed to get hot deals: {str(e)}")
        return {"exc": "Something went wrong!"}
    

@frappe.whitelist()
def add_to_wishlist(item_code):
    try:
        # Check if item already exists in the wishlist
        if frappe.db.exists("Wishlist Item", {"item_code": item_code, "parent": frappe.session.user}):
            return {"message": "Item already in wishlist"}
        
        # Fetch Web Item Data by item_code
        web_item_data = frappe.db.get_value(
            "Website Item",
            {"item_code": item_code},
            [
                "website_image",
                "website_warehouse",
                "name",
                "web_item_name",
                "item_name",
                "item_group",
                "route",
            ],
            as_dict=1,
        )

        # Prepare the item to be added
        wished_item_dict = {
            "item_code": item_code,
            "item_name": web_item_data.get("item_name"),
            "item_group": web_item_data.get("item_group"),
            "website_item": web_item_data.get("name"),
            "web_item_name": web_item_data.get("web_item_name"),
            "image": web_item_data.get("website_image"),
            "warehouse": web_item_data.get("website_warehouse"),
            "route": web_item_data.get("route"),
        }

        # Add the item to the wishlist
        if not frappe.db.exists("Wishlist", frappe.session.user):
            # initialise wishlist
            wishlist = frappe.get_doc({"doctype": "Wishlist"})
            wishlist.user = frappe.session.user
            wishlist.append("items", wished_item_dict)
            wishlist.save(ignore_permissions=True)
        else:
            wishlist = frappe.get_doc("Wishlist", frappe.session.user)
            item = wishlist.append("items", wished_item_dict)
            item.db_insert()

        # Commit the transaction to the database
        frappe.db.commit()
        
        if hasattr(frappe.local, "cookie_manager"):
            frappe.local.cookie_manager.set_cookie("wish_count", str(len(wishlist.items)))

        return {"message": "Item added to wishlist successfully"}

    except frappe.ValidationError as e:
        frappe.log_error(f"Error adding item to wishlist: {e}")
        return {"message": "Failed to add item to wishlist", "error": str(e)}

    except Exception as e:
        frappe.log_error(f"Unexpected error: {e}")
        return {"message": "An unexpected error occurred", "error": str(e)}
    

@frappe.whitelist()
def remove_from_wishlist(item_code):
    try:
        # Check if the item exists in the user's wishlist
        if frappe.db.exists("Wishlist Item", {"item_code": item_code, "parent": frappe.session.user}):
            # Delete the wishlist item
            frappe.db.delete("Wishlist Item", {"item_code": item_code, "parent": frappe.session.user})
            frappe.db.commit()  # Ensure the transaction is committed

            # Fetch updated wishlist items count for the user
            wishlist_items = frappe.db.get_values("Wishlist Item", filters={"parent": frappe.session.user}, fieldname="name")

            # Update the wish count in cookies
            if hasattr(frappe.local, "cookie_manager"):
                frappe.local.cookie_manager.set_cookie("wish_count", str(len(wishlist_items)))

            return {"message": "Item removed from wishlist", "wish_count": len(wishlist_items)}
        else:
            return {"message": "Item not found in wishlist", "wish_count": 0}

    except frappe.DoesNotExistError:
        frappe.log_error(f"Wishlist Item with item_code {item_code} not found for user {frappe.session.user}")
        return {"error": "Item does not exist in the wishlist"}

    except frappe.ValidationError as e:
        frappe.log_error(f"Validation error while removing item from wishlist: {e}")
        return {"error": "There was a validation error"}

    except Exception as e:
        frappe.log_error(f"Unexpected error while removing item from wishlist: {str(e)}")
        return {"error": "An unexpected error occurred"}


@frappe.whitelist()
def get_wishlist():
    try:
        # Fetch all wishlist items for the current user
        wishlist_items = frappe.get_all(
            "Wishlist Item",
            filters={"parent": frappe.session.user},
            fields=["item_code", "item_name", "description", "image", "warehouse"]
        )

        # Loop through wishlist items and append price
        for item in wishlist_items:
            get_stock_availability(item)
            product_info = get_product_info_for_website(item["item_code"], skip_quotation_creation=True).get("product_info")
            if product_info and product_info["price"]:
                item.update({
                    "formatted_mrp": product_info["price"].get("formatted_mrp"),
                    "formatted_price": product_info["price"].get("formatted_price"),
                    "price_list_rate": product_info["price"].get("price_list_rate")
                })
            if product_info["price"].get("discount_percent"):
                item.update({
                    "discount_percent" : flt(product_info["price"].discount_percent)
                })
            if item.formatted_mrp:
                item.update({
                    "discount" : product_info["price"].get("formatted_discount_percent") or product_info["price"].get(
                        "formatted_discount_rate"
                    )
                })

        return wishlist_items

    except Exception as e:
        frappe.log_error(f"Error retrieving wishlist for user {frappe.session.user}: {str(e)}")
        return {"error": "An error occurred while retrieving the wishlist"}


@frappe.whitelist(allow_guest=True)
def generate_session_id():
    # Get client IP address
    client_ip = frappe.get_request_header('X-Forwarded-For') or frappe.get_request_header('X-Real-IP') or frappe.local.request.remote_addr
    
    # Get user agent (browser and device info)
    user_agent = frappe.get_request_header('User-Agent')
    
    # Combine IP address and user-agent into a unique string
    unique_string = f"{client_ip}-{user_agent}-{uuid.uuid4()}"
    
    # Hash the unique string to generate session ID
    session_id = hashlib.sha256(unique_string.encode()).hexdigest()

    frappe.local.cookie_manager = CookieManager()
    frappe.local.cookie_manager.set_cookie('session_id', session_id, max_age=365 * 24 * 60 * 60, httponly=True, secure=False)
    
    # # Set session_id in response header as a cookie
    # frappe.set_cookie('session_id', session_id, max_age=365 * 24 * 60 * 60, httponly=True, secure=False)
    
    # Return the session ID as a response as well, if needed for logging
    return {
        'session_id': session_id,
        'ip_address': client_ip,
        'user_agent': user_agent
    }


@frappe.whitelist(allow_guest=True)
def download_app():
    user_agent = frappe.request.headers.get('User-Agent')

    if 'Android' in user_agent:
        # Redirect to Google Play Store
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = "https://play.google.com/store/apps/details?id=com.amazon.mShop.android.shopping"
    elif 'iPhone' in user_agent or 'iPad' in user_agent:
        # Redirect to Apple App Store
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = "https://apps.apple.com/us/app/amazon-shopping/id297606951"
    else:
        # Fallback URL (e.g., your website or app info page)
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = "https://keno.today"
