"""
Odoo: approved records become vendor bills (account.move, type in_invoice) through Odoo's external JSON-RPC API. Signing in is a database
name, a user and that user's API key. The supplier is found or created, and a bill already filed with the same vendor reference is not
filed twice. Written from Odoo's public external API documentation and tested against a mock.
"""
import itertools
from typing import Any, Dict, List, Optional, Tuple
from app.services.destinations import http
from app.services.destinations.accounting import Bill, BillAdapter, create_vendors_field, done, fail, failed_reply, fmt
from app.services.destinations.base import Field, SendContext, TestResult, register

_ids = itertools.count(1)


class OdooAdapter(BillAdapter):
    kind = "odoo"
    label = "Odoo"
    blurb = "Create a vendor bill in Odoo for each approved invoice."
    fields = [
        Field("url", "Odoo address", "url", required=True, placeholder="https://mycompany.odoo.com"),
        Field("database", "Database", "text", required=True),
        Field("login", "User", "text", required=True, help="The login of the user the bills are created as."),
        Field("api_key", "API key", "password", secret=True, required=True, help="Preferences > Account Security > New API Key."),
        create_vendors_field(),
    ]

    async def rpc(self, c: Dict[str, Any], service: str, method: str, args: List[Any], kwargs: Optional[Dict[str, Any]] = None) -> Tuple[Optional[Any], Optional[str], http.Reply]:
        """(the result, None, reply) or (None, the reason, reply). Odoo answers HTTP 200 even for errors, which come in the body."""
        payload = {"jsonrpc": "2.0", "method": "call", "id": next(_ids), "params": {"service": service, "method": method, "args": args}}
        reply = await http.call("POST", f"{c['url'].rstrip('/')}/jsonrpc", headers={"Content-Type": "application/json", "User-Agent": "Syruss-Delivery/1"}, json_body=payload)
        if not reply.ok:
            return None, None, reply
        body = reply.json() or {}
        if body.get("error"):
            data = body["error"].get("data") or {}
            return None, str(data.get("message") or body["error"].get("message") or "Odoo refused the request.")[:300], reply
        return body.get("result"), None, reply

    async def execute(self, c: Dict[str, Any], s: Dict[str, Any], uid: int, model: str, method: str, args: List[Any], kwargs: Optional[Dict[str, Any]] = None):
        return await self.rpc(c, "object", "execute_kw", [c["database"], uid, s["api_key"], model, method, args, kwargs or {}])

    async def sign_in(self, c: Dict[str, Any], s: Dict[str, Any]) -> Tuple[Optional[int], Optional[str], Optional[http.Reply]]:
        uid, why, reply = await self.rpc(c, "common", "authenticate", [c["database"], c["login"], s.get("api_key", ""), {}])
        if why:
            return None, why, reply
        if not reply.ok:
            return None, http.failure_from(reply, "Odoo")[0], reply
        if not uid:
            return None, "Odoo refused the database, user or API key.", reply
        return int(uid), None, reply

    async def test(self, config: Dict[str, Any], secrets: Dict[str, Any], destination_id: Optional[str] = None) -> TestResult:
        uid, why, _ = await self.sign_in(config, secrets)
        return TestResult(False, why or "Could not sign in to Odoo.") if uid is None else TestResult(True, "Signed in to Odoo.")

    async def post_bill(self, ctx: SendContext, bill: Bill) -> SendResult:
        c, s = ctx.config, ctx.secrets
        uid, why, reply = await self.sign_in(c, s)
        if uid is None:
            retryable = reply is not None and (reply.error is not None or (reply.status or 0) >= 500)
            return fail(why or "Could not sign in to Odoo.", retryable=retryable)
        log = []

        def fault(why: Optional[str], reply: http.Reply):
            return failed_reply(reply, "Odoo", "\n".join(log)) if not reply.ok else fail(why or "Odoo refused the request.", request="\n".join(log), reply=reply)

        partner = int(bill.vendor_id) if bill.vendor_id and bill.vendor_id.isdigit() else None
        if partner is None:
            found, why, reply = await self.execute(c, s, uid, "res.partner", "search", [[["name", "=", bill.vendor]]], {"limit": 1})
            log.append(f"search partner '{bill.vendor}'")
            if why or not reply.ok:
                return fault(why, reply)
            if found:
                partner = found[0]
            elif c.get("create_suppliers", True):
                partner, why, reply = await self.execute(c, s, uid, "res.partner", "create", [{"name": bill.vendor, "company_type": "company"}])
                log.append("create partner")
                if why or not reply.ok:
                    return fault(why, reply)
            else:
                return fail(f"The supplier '{bill.vendor}' is not in Odoo, and creating suppliers is turned off.", request="\n".join(log))

        dup, why, reply = await self.execute(c, s, uid, "account.move", "search", [[["move_type", "=", "in_invoice"], ["partner_id", "=", partner], ["ref", "=", bill.number]]], {"limit": 1})
        if not why and reply.ok and dup:
            return done(str(dup[0]), "\n".join(log) + f"\nBill {bill.number} already exists", reply)

        values: Dict[str, Any] = {
            "move_type": "in_invoice", "partner_id": partner, "ref": bill.number, "invoice_date": bill.date,
            "invoice_line_ids": [[0, 0, {"name": l.description, "quantity": float(l.quantity), "price_unit": float(fmt(l.unit_price))}] for l in bill.lines],
        }
        if bill.due:
            values["invoice_date_due"] = bill.due
        if bill.memo:
            values["narration"] = bill.memo
        if bill.currency:
            currency, why, reply = await self.execute(c, s, uid, "res.currency", "search", [[["name", "=", bill.currency]]], {"limit": 1})
            if why or not reply.ok:
                return fault(why, reply)
            if not currency:
                return fail(f"Odoo has no active currency called {bill.currency}.", request="\n".join(log))
            values["currency_id"] = currency[0]
        made, why, reply = await self.execute(c, s, uid, "account.move", "create", [values])
        log.append(f"create bill {bill.number}")
        if why or not reply.ok or not made:
            return fault(why, reply)
        return done(str(made), "\n".join(log), reply)


register(OdooAdapter())
