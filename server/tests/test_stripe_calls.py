"""
The calls to Stripe themselves, fed the stripe library's real objects (not dicts): from stripe-python 13 a Stripe object
has no .get, and the other card tests replace these calls, so they wouldn't notice.
"""
import stripe
from app.services.billing.methods import card


def _session(**fields):
    return stripe.checkout.Session.construct_from({"id": "cs_test_1", "object": "checkout.session", **fields}, "sk_test_x")


def test_a_checkout_page_is_read_as_stripe_has_it(monkeypatch):
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", classmethod(lambda cls, sid, api_key=None: _session(
        status="complete", payment_status="paid", amount_total=4900, payment_intent="pi_1", customer="cus_1")))
    assert card.get_checkout("cs_test_1") == {"status": "complete", "payment_status": "paid", "amount_total": 4900,
                                              "payment_intent": "pi_1", "customer": "cus_1"}
    # expanded, or not there yet
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", classmethod(lambda cls, sid, api_key=None: _session(
        status="open", payment_status="unpaid", amount_total=None, payment_intent={"id": "pi_2", "object": "payment_intent"})))
    assert card.get_checkout("cs_test_1")["payment_intent"] == "pi_2"
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", classmethod(lambda cls, sid, api_key=None: _session(status="expired")))
    assert card.get_checkout("cs_test_1") == {"status": "expired", "payment_status": None, "amount_total": 0, "payment_intent": None, "customer": None}


def test_the_kept_card_is_read_from_the_payment(monkeypatch):
    intent = stripe.PaymentIntent.construct_from({"id": "pi_1", "object": "payment_intent", "customer": "cus_1", "payment_method": {
        "id": "pm_1", "object": "payment_method", "card": {"brand": "visa", "last4": "4242"}}}, "sk_test_x")
    monkeypatch.setattr(stripe.PaymentIntent, "retrieve", classmethod(lambda cls, pid, api_key=None, expand=None: intent))
    assert card.card_of("pi_1") == {"customer": "cus_1", "payment_method": "pm_1", "label": "Visa ending 4242"}


def test_a_renewal_charge_reads_stripes_answer(monkeypatch):
    answer = stripe.PaymentIntent.construct_from({"id": "pi_9", "object": "payment_intent", "status": "succeeded", "amount_received": 4900}, "sk_test_x")
    monkeypatch.setattr(stripe.PaymentIntent, "create", classmethod(lambda cls, **kw: answer))
    assert card.charge(amount_cents=4900, currency="USD", customer="cus_1", payment_method="pm_1", invoice_id="inv_1", tenant_id="t_1") == {
        "id": "pi_9", "status": "succeeded", "amount_received": 4900}
