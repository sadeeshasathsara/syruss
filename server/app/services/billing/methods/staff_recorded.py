"""Cash or a cheque, handed to Syruss: staff record it against the invoice. Customers don't pay this way in the app."""
from app.services.billing.methods.base import Method, register


class StaffRecorded(Method):
    key, label, customer_can_use = "staff_recorded", "Cash or cheque (recorded by Syruss)", False


register(StaffRecorded())
