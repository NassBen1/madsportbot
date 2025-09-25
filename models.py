# models.py
from collections import defaultdict

# panier: {user_id: [{"id":..., "name":..., "club":"", "season":"", "color":"", "size":"", "custom":{"name":"","number":""}, "qty":1, "price_cents":5999}]}
carts = defaultdict(list)

def add_to_cart(user_id, item):
    for i in carts[user_id]:
        same = (
            i["id"] == item["id"]
            and i.get("color") == item.get("color")
            and i.get("size") == item.get("size")
            and (i.get("custom") or {}) == (item.get("custom") or {})
        )
        if same:
            i["qty"] += item.get("qty", 1)
            return
    carts[user_id].append(item)

def remove_from_cart(user_id, index):
    if 0 <= index < len(carts[user_id]):
        carts[user_id].pop(index)

def empty_cart(user_id):
    carts[user_id].clear()

def cart_total_cents(user_id):
    return sum(i["price_cents"] * i["qty"] for i in carts[user_id])
