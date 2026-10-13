"""Brønnøysund Register Centre (Enhetsregisteret): identity, roles, workplaces, group, signature rules, change feed.

All endpoints are open, keyless and licensed under NLOD 2.0. Every claim is tied to the
organisation number by the endpoint itself.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from ..codes import code_name
from ..http import Fetcher
from ..model import OFFICIAL_REGISTRY, CompanyContext, Evidence, Snapshot

API = "https://data.brreg.no/enhetsregisteret/api"
LICENCE = "NLOD 2.0, Brønnøysundregistrene"
JSON_HEADERS = {"Accept": "application/json"}
HUMAN = "https://virksomhet.brreg.no/oppslag/enheter/{org}"


def span_of(obj: Any, *keys: str) -> str:
    """A compact JSON excerpt of the keys that support a claim."""
    if isinstance(obj, dict):
        part = {k: obj[k] for k in keys if k in obj} if keys else obj
    else:
        part = obj
    return json.dumps(part, ensure_ascii=False, separators=(",", ":"))[:400]


def _addr(a: dict | None) -> str | None:
    if not a:
        return None
    lines = [x for x in a.get("adresse") or [] if x]
    tail = " ".join(x for x in (a.get("postnummer"), a.get("poststed")) if x)
    parts = lines + ([tail] if tail else [])
    if a.get("landkode") and a.get("landkode") != "NO":
        parts.append(a.get("land") or a["landkode"])
    return ", ".join(parts) or None


# ---------------------------------------------------------------- entity
async def fetch_entity(f: Fetcher, ctx: CompanyContext, batch_record: tuple[Snapshot, dict] | None = None) -> None:
    url = f"{API}/enheter/{ctx.org}"
    snap: Snapshot | None = None
    rec: dict | None = None
    if batch_record is not None:
        snap, rec = batch_record
    else:
        snap = await f.get(url, headers=JSON_HEADERS)
        ctx.requests += 1
        if snap.status == 404:
            # Could be a sub-unit number or removed from open data.
            sub = await f.get(f"{API}/underenheter/{ctx.org}", headers=JSON_HEADERS)
            ctx.requests += 1
            if sub.status == 200:
                snap, rec = sub, sub.json()
                rec["_is_subunit"] = True
            else:
                ev = ctx.add_evidence(snap, source_class=OFFICIAL_REGISTRY, method="brreg_entity_v1", licence=LICENCE,
                                      span="HTTP 404: no entity with this organisation number")
                ctx.mark("legal_name", "identity", "not_available", ev, note="Enhetsregisteret has no record")
                ctx.module_states["registry"] = "not_available"
                return
        elif snap.status == 410:
            rec = snap.json()
            rec["_gone"] = True
        elif snap.status != 200:
            ctx.error("registry", f"HTTP {snap.status} for {url}")
            ctx.mark("legal_name", "identity", "failed", note=f"registry HTTP {snap.status}")
            ctx.module_states["registry"] = "failed"
            return
        else:
            rec = snap.json()
    assert rec is not None and snap is not None
    ctx.registry = rec
    ctx.module_states["registry"] = "available"
    emit_entity_claims(ctx, snap, rec)


def emit_entity_claims(ctx: CompanyContext, snap: Snapshot, e: dict) -> None:
    org = ctx.org
    batch_note = None if snap.url.endswith(f"/{org}") else f"Batch lookup; single record: {API}/enheter/{org}"

    def ev(*keys: str, effective: str | None = None) -> Evidence:
        return ctx.add_evidence(snap, source_class=OFFICIAL_REGISTRY, span=span_of(e, *keys), effective_date=effective,
                                method="brreg_entity_v1", licence=LICENCE, note=batch_note,
                                anchor=("organisasjonsnummer", org) if batch_note else None)

    m = {"method": "registry_endpoint", "detail": f"Enhetsregisteret record for {org}"}
    if e.get("_gone") or e.get("slettedato"):
        ctx.add_claim("legal_name", "identity", e.get("navn"), ev("navn"), match=m)
        ctx.add_claim("deleted_date", "identity", e.get("slettedato"), ev("slettedato"), as_of=e.get("slettedato"), match=m)
        ctx.facts["deleted"] = True
        return
    ctx.add_claim("legal_name", "identity", e["navn"], ev("navn", "organisasjonsnummer"), match=m)
    if e.get("historiskeNavn"):
        ctx.add_claim("former_names", "identity", e["historiskeNavn"], ev("historiskeNavn"), match=m)
    if e.get("organisasjonsform"):
        f = e["organisasjonsform"]
        ctx.add_claim("legal_form", "identity", {"code": f.get("kode"), "name": f.get("beskrivelse")},
                      ev("organisasjonsform"), match=m)
    for key, field in (("stiftelsesdato", "incorporation_date"), ("registreringsdatoEnhetsregisteret", "registration_date"),
                       ("registreringsdatoForetaksregisteret", "business_register_date"), ("vedtektsdato", "articles_date")):
        if e.get(key):
            ctx.add_claim(field, "identity", e[key], ev(key), as_of=e[key], match=m)
    if e.get("forretningsadresse"):
        ctx.add_claim("business_address", "identity", _addr(e["forretningsadresse"]), ev("forretningsadresse"), match=m)
        if e["forretningsadresse"].get("kommune"):
            ctx.add_claim("municipality", "identity", {"name": e["forretningsadresse"]["kommune"],
                                                       "number": e["forretningsadresse"].get("kommunenummer")},
                          ev("forretningsadresse"), match=m)
    if e.get("beliggenhetsadresse"):
        ctx.add_claim("business_address", "identity", _addr(e["beliggenhetsadresse"]), ev("beliggenhetsadresse"), match=m)
    if e.get("postadresse"):
        ctx.add_claim("postal_address", "identity", _addr(e["postadresse"]), ev("postadresse"), match=m)
    for i, key in enumerate(("naeringskode1", "naeringskode2", "naeringskode3")):
        if e.get(key):
            code = e[key].get("kode")
            ctx.add_claim("industry" if i == 0 else "secondary_industry", "identity",
                          {"code": code, "name": e[key].get("beskrivelse") or code_name("nace", code)}, ev(key), match=m)
    if e.get("institusjonellSektorkode"):
        s = e["institusjonellSektorkode"]
        ctx.add_claim("institutional_sector", "identity", {"code": s.get("kode"), "name": s.get("beskrivelse")},
                      ev("institusjonellSektorkode"), match=m)
    if e.get("vedtektsfestetFormaal"):
        ctx.add_claim("statutory_purpose", "identity", " ".join(e["vedtektsfestetFormaal"]), ev("vedtektsfestetFormaal"),
                      as_of=e.get("vedtektsdato"), match=m)
    if e.get("aktivitet"):
        ctx.add_claim("registered_activity", "identity", " ".join(e["aktivitet"]), ev("aktivitet"), match=m)
    if e.get("antallAnsatte") is not None and e.get("harRegistrertAntallAnsatte", True):
        when = e.get("registreringsdatoAntallAnsatteNAVAaregisteret") or e.get("registreringsdatoAntallAnsatteEnhetsregisteret")
        ctx.add_claim("employees_registered", "identity", e["antallAnsatte"],
                      ev("antallAnsatte", "registreringsdatoAntallAnsatteNAVAaregisteret",
                         "registreringsdatoAntallAnsatteEnhetsregisteret", effective=when), as_of=when, match=m)
    elif e.get("harRegistrertAntallAnsatte") is False:
        ctx.add_claim("employees_registered", "identity", None, ev("harRegistrertAntallAnsatte"), availability="not_available",
                      note="No employees registered in the Aa-register", match=m)
    elif "antallAnsatte" not in e and e.get("harRegistrertAntallAnsatte"):
        ctx.add_claim("employees_registered", "identity", "1-4", ev("harRegistrertAntallAnsatte"), match=m,
                      note="Brreg withholds exact counts between 1 and 4 employees for privacy")
    if e.get("kapital"):
        k = e["kapital"]
        ctx.add_claim("share_capital", "identity", {"amount": k.get("belop"), "currency": k.get("valuta"),
                                                    "shares": k.get("antallAksjer"), "type": k.get("type")},
                      ev("kapital", effective=k.get("innfortDato")), as_of=k.get("innfortDato"), match=m)
    flags = {k: e[k] for k in ("registrertIMvaregisteret", "registrertIForetaksregisteret", "registrertIStiftelsesregisteret",
                               "registrertIFrivillighetsregisteret", "registrertIPartiregisteret") if k in e}
    if flags:
        ctx.add_claim("register_memberships", "identity", flags, ev(*flags.keys()), match=m)
    status = {k: e[k] for k in ("konkurs", "konkursdato", "underAvvikling", "underAvviklingDato",
                                "underTvangsavviklingEllerTvangsopplosning", "underRekonstruksjonsforhandlingDato") if k in e}
    if status:
        ctx.add_claim("legal_status", "identity", status, ev(*status.keys()), match=m)
    if e.get("overordnetEnhet"):
        ctx.add_claim("parent_organisation_number", "identity", e["overordnetEnhet"], ev("overordnetEnhet"), match=m)
    if e.get("erIKonsern") is not None:
        ctx.add_claim("part_of_group", "identity", e["erIKonsern"], ev("erIKonsern"), match=m)
    if e.get("sisteInnsendteAarsregnskap"):
        ctx.add_claim("latest_filed_accounts_year", "financials", e["sisteInnsendteAarsregnskap"],
                      ev("sisteInnsendteAarsregnskap"), match=m)
    if e.get("fravalgRevisjonDato"):
        ctx.add_claim("audit_opt_out_date", "financials", e["fravalgRevisjonDato"], ev("fravalgRevisjonDato"),
                      as_of=e["fravalgRevisjonDato"], match=m)
    site = (e.get("hjemmeside") or "").strip().lower()
    platform = next((name for host, name in (("facebook.com", "facebook"), ("instagram.com", "instagram"),
                                             ("linkedin.com", "linkedin"), ("youtube.com", "youtube"), ("tiktok.com", "tiktok"))
                     if host in site), None)
    if platform:
        url = site if site.startswith("http") else "https://" + site
        ctx.add_claim("social_profile", "web_presence", {"platform": platform, "url": url}, ev("hjemmeside"),
                      match={"method": "registry_declared", "detail": "The company registered this profile as its website in Enhetsregisteret"})
    for key, field in (("hjemmeside", "registry_website"), ("epostadresse", "registry_email"),
                       ("telefon", "registry_phone"), ("mobil", "registry_mobile")):
        if e.get(key):
            ctx.add_claim(field, "identity", e[key], ev(key), match=m)


async def fetch_entities_batch(f: Fetcher, orgs: list[str]) -> dict[str, tuple[Snapshot, dict]]:
    """One request per 100 org numbers (the API accepts 2,000, but a short URL keeps evidence readable)."""
    out: dict[str, tuple[Snapshot, dict]] = {}
    for i in range(0, len(orgs), 100):
        chunk = orgs[i:i + 100]
        url = f"{API}/enheter?size={len(chunk)}&organisasjonsnummer={','.join(chunk)}"
        snap = await f.get(url, headers=JSON_HEADERS)
        if snap.status != 200:
            continue
        for rec in (snap.json().get("_embedded") or {}).get("enheter", []):
            out[rec["organisasjonsnummer"]] = (snap, rec)
    return out


# ---------------------------------------------------------------- roles
ROLE_FIELDS = {
    "DAGL": "ceo", "LEDE": "board_chair", "NEST": "board_deputy_chair", "MEDL": "board_member",
    "VARA": "board_deputy_member", "OBS": "board_observer", "REVI": "auditor", "REGN": "accountant",
    "KONT": "contact_person", "INNH": "owner", "DTPR": "partner", "DTSO": "partner", "KOMP": "general_partner",
    "FFØR": "business_manager", "BOBE": "bankruptcy_trustee", "REPR": "norwegian_representative",
    "SAM": "co_owner", "HLED": "general_meeting_chair",
}


async def fetch_roles(f: Fetcher, ctx: CompanyContext) -> None:
    url = f"{API}/enheter/{ctx.org}/roller"
    snap = await f.get(url, headers=JSON_HEADERS)
    ctx.requests += 1
    if snap.status == 404:
        ev = ctx.add_evidence(snap, source_class=OFFICIAL_REGISTRY, method="brreg_roles_v1", licence=LICENCE, span="HTTP 404")
        ctx.mark("ceo", "leadership", "not_available", ev, note="No roles registered")
        ctx.module_states["roles"] = "not_available"
        return
    if snap.status != 200:
        ctx.error("roles", f"HTTP {snap.status}")
        ctx.mark("ceo", "leadership", "failed", note=f"roles HTTP {snap.status}")
        ctx.module_states["roles"] = "failed"
        return
    data = snap.json()
    m = {"method": "registry_endpoint", "detail": f"Roles registered on {ctx.org}"}
    found = set()
    for group in data.get("rollegrupper", []):
        changed = group.get("sistEndret")
        for role in group.get("roller", []):
            if role.get("fratraadt") or role.get("avregistrert"):
                continue
            code = (role.get("type") or {}).get("kode")
            field = ROLE_FIELDS.get(code, "other_role")
            holder: dict[str, Any] = {"role": (role.get("type") or {}).get("beskrivelse"), "role_code": code,
                                      "group": (group.get("type") or {}).get("beskrivelse")}
            if role.get("person"):
                n = role["person"].get("navn") or {}
                holder["name"] = " ".join(x for x in (n.get("fornavn"), n.get("mellomnavn"), n.get("etternavn")) if x)
                if role["person"].get("erDoed"):
                    continue
                span = {"navn": n, "type": role.get("type")}  # birth date deliberately left out of the span
            elif role.get("enhet"):
                en = role["enhet"]
                holder["name"] = " ".join(en.get("navn") or [])
                holder["organisation_number"] = en.get("organisasjonsnummer")
                span = {"organisasjonsnummer": en.get("organisasjonsnummer"), "navn": en.get("navn"), "type": role.get("type")}
            else:
                continue
            ev = ctx.add_evidence(snap, source_class=OFFICIAL_REGISTRY, span=json.dumps(span, ensure_ascii=False),
                                  effective_date=changed, method="brreg_roles_v1", licence=LICENCE)
            ctx.add_claim(field, "leadership", holder, ev, as_of=changed, match=m)
            found.add(field)
    ctx.facts["role_names"] = [c.value.get("name") for c in ctx.claims if c.section == "leadership" and c.availability == "available"]
    if "ceo" not in found:
        ev = ctx.add_evidence(snap, source_class=OFFICIAL_REGISTRY, method="brreg_roles_v1", licence=LICENCE,
                              span="No DAGL role in rollegrupper")
        ctx.mark("ceo", "leadership", "not_available", ev, note="No general manager registered (not required for every company form)")
    ctx.module_states["roles"] = "available"


# ---------------------------------------------------------------- workplaces
async def fetch_subunits(f: Fetcher, ctx: CompanyContext) -> None:
    url = f"{API}/underenheter?overordnetEnhet={ctx.org}&size=100"
    snap = await f.get(url, headers=JSON_HEADERS)
    ctx.requests += 1
    if snap.status != 200:
        ctx.error("locations", f"HTTP {snap.status}")
        ctx.mark("registered_workplace", "locations", "failed", note=f"subunits HTTP {snap.status}")
        return
    items = (snap.json().get("_embedded") or {}).get("underenheter", [])
    m = {"method": "registry_endpoint", "detail": f"Sub-units with overordnetEnhet={ctx.org}"}
    if not items:
        ev = ctx.add_evidence(snap, source_class=OFFICIAL_REGISTRY, method="brreg_subunits_v1", licence=LICENCE,
                              span='"totalElements":0')
        ctx.mark("registered_workplace", "locations", "not_available", ev, note="No registered sub-units")
        return
    for u in items:
        if u.get("nedleggelsesdato"):
            continue
        value = {"organisation_number": u.get("organisasjonsnummer"), "name": u.get("navn"),
                 "address": _addr(u.get("beliggenhetsadresse") or u.get("postadresse")),
                 "municipality": (u.get("beliggenhetsadresse") or {}).get("kommune"),
                 "industry": (u.get("naeringskode1") or {}).get("beskrivelse"),
                 "employees": u.get("antallAnsatte"), "start_date": u.get("oppstartsdato")}
        ev = ctx.add_evidence(snap, source_class=OFFICIAL_REGISTRY, method="brreg_subunits_v1", licence=LICENCE,
                              span=span_of(u, "organisasjonsnummer", "navn", "beliggenhetsadresse", "antallAnsatte"),
                              effective_date=u.get("oppstartsdato"), anchor=("organisasjonsnummer", u.get("organisasjonsnummer")))
        ctx.add_claim("registered_workplace", "locations", {k: v for k, v in value.items() if v is not None}, ev,
                      as_of=u.get("oppstartsdato"), match=m)


# ---------------------------------------------------------------- group
async def fetch_group(f: Fetcher, ctx: CompanyContext) -> None:
    url = f"{API}/konsernstruktur/{ctx.org}"
    snap = await f.get(url, headers=JSON_HEADERS)
    ctx.requests += 1
    if snap.status != 200:
        return
    data = snap.json()
    m = {"method": "registry_endpoint", "detail": f"Group structure for {ctx.org}"}
    nodes: list[dict] = []

    def walk(node: dict) -> None:
        nodes.append(node)
        for child in node.get("children") or []:
            walk(child)

    walk(data)
    # The root is the ultimate parent; each node names its direct parent.
    me = next((n for n in nodes if n.get("organisasjonsnummer") == ctx.org), None)
    if me and me.get("parentOrganisasjonsnummer"):
        ev = ctx.add_evidence(snap, source_class=OFFICIAL_REGISTRY, method="brreg_group_v1", licence=LICENCE,
                              span=span_of(me, "parentOrganisasjonsnummer", "parentNavn", "organisasjonsnummer", "grunnlag", "dato"),
                              effective_date=me.get("dato"), anchor=("organisasjonsnummer", ctx.org))
        ctx.add_claim("group_parent", "identity", {"organisation_number": me["parentOrganisasjonsnummer"],
                                                   "name": me.get("parentNavn"), "ownership": me.get("grunnlag")},
                      ev, as_of=me.get("dato"), match=m)
    if data.get("organisasjonsnummer") and data["organisasjonsnummer"] != ctx.org and \
            (not me or data["organisasjonsnummer"] != me.get("parentOrganisasjonsnummer")):
        ev = ctx.add_evidence(snap, source_class=OFFICIAL_REGISTRY, method="brreg_group_v1", licence=LICENCE,
                              span=span_of(data, "organisasjonsnummer", "navn"))
        ctx.add_claim("group_ultimate_parent", "identity", {"organisation_number": data["organisasjonsnummer"],
                                                            "name": data.get("navn")}, ev, match=m)
    children = [n for n in nodes if n.get("parentOrganisasjonsnummer") == ctx.org]
    for c in children[:50]:
        ev = ctx.add_evidence(snap, source_class=OFFICIAL_REGISTRY, method="brreg_group_v1", licence=LICENCE,
                              span=span_of(c, "organisasjonsnummer", "navn", "knytningsform", "grunnlag", "dato"),
                              effective_date=c.get("dato"), anchor=("organisasjonsnummer", c["organisasjonsnummer"]))
        ctx.add_claim("group_subsidiary", "identity",
                      {"organisation_number": c["organisasjonsnummer"], "name": c.get("navn"),
                       "relationship": (c.get("knytningsform") or {}).get("beskrivelse"), "ownership": c.get("grunnlag")},
                      ev, as_of=c.get("dato"), match=m)


# ---------------------------------------------------------------- signature rules
async def fetch_signature(f: Fetcher, ctx: CompanyContext) -> None:
    url = f"https://data.brreg.no/fullmakt/enheter/{ctx.org}/signatur"
    snap = await f.get(url, headers=JSON_HEADERS)
    ctx.requests += 1
    if snap.status != 200:
        return
    data = snap.json()
    text = (((data.get("signeringsGrunnlag") or {}).get("signaturProkuraRoller") or {}).get("signaturProkuraFritekst"))
    if text:
        ev = ctx.add_evidence(snap, source_class=OFFICIAL_REGISTRY, method="brreg_signature_v1", licence=LICENCE,
                              span=f'"signaturProkuraFritekst":"{text}"')
        ctx.add_claim("signature_rule", "leadership", text, ev,
                      match={"method": "registry_endpoint", "detail": f"Signature authority for {ctx.org}"})


# ---------------------------------------------------------------- change feed
PATH_WORDS = {
    "navn": "name", "historiskeNavn": "name history", "forretningsadresse": "business address", "postadresse": "postal address",
    "antallAnsatte": "employee count", "naeringskode1": "industry code", "naeringskode2": "industry code",
    "hjemmeside": "website", "epostadresse": "email", "telefon": "phone", "mobil": "mobile",
    "sisteInnsendteAarsregnskap": "latest filed annual accounts", "kapital": "share capital", "vedtektsdato": "articles of association",
    "vedtektsfestetFormaal": "statutory purpose", "konkurs": "bankruptcy status", "underAvvikling": "liquidation status",
    "organisasjonsform": "legal form", "registrertIMvaregisteret": "VAT registration", "aktivitet": "registered activity",
    "institusjonellSektorkode": "sector code", "stiftelsesdato": "incorporation date", "erIKonsern": "group membership",
    "fravalgRevisjonDato": "audit opt-out", "fravalgRevisjonBeslutningsDato": "audit opt-out", "naeringskode3": "industry code",
    "beliggenhetsadresse": "location address", "registrertIForetaksregisteret": "Register of Business Enterprises status",
    "registrertIFrivillighetsregisteret": "Register of Non-Profit Organisations status", "harRegistrertAntallAnsatte": "employee count",
    "underTvangsavviklingEllerTvangsopplosning": "forced dissolution status", "konkursdato": "bankruptcy status",
    "underAvviklingDato": "liquidation status", "paategninger": "registry annotations", "overordnetEnhet": "parent unit",
    "maalform": "written language", "frivilligMvaRegistrertBeskrivelser": "voluntary VAT registration",
    "underRekonstruksjonsforhandlingDato": "restructuring negotiation", "hjelpeenhetskode": "auxiliary unit code",
}


def describe_paths(changes: list[dict]) -> list[str]:
    seen: list[str] = []
    for ch in changes:
        top = (ch.get("path") or "/").strip("/").split("/")[0]
        if top.startswith("registreringsdato"):
            continue
        word = PATH_WORDS.get(top, top)
        if word and word not in seen:
            seen.append(word)
    return seen


async def fetch_change_feed(f: Fetcher, orgs: list[str], days: int = 365) -> dict[str, tuple[Snapshot, list[dict]]]:
    """Registry updates for the batch in the last `days` days, with the changed fields."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    out: dict[str, tuple[Snapshot, list[dict]]] = {}
    for i in range(0, len(orgs), 100):
        chunk = orgs[i:i + 100]
        url = (f"{API}/oppdateringer/enheter?dato={since}&organisasjonsnummer={','.join(chunk)}"
               f"&includeChanges=true&size=2000")
        try:
            snap = await f.get(url, headers=JSON_HEADERS)
        except Exception:
            continue
        if snap.status != 200:
            continue
        for item in (snap.json().get("_embedded") or {}).get("oppdaterteEnheter", []):
            org = item.get("organisasjonsnummer")
            out.setdefault(org, (snap, []))[1].append(item)
    return out


def emit_change_claims(ctx: CompanyContext, snap: Snapshot, items: list[dict]) -> None:
    m = {"method": "registry_endpoint", "detail": f"Enhetsregisteret update feed filtered on {ctx.org}"}
    for item in sorted(items, key=lambda x: x.get("dato", ""), reverse=True)[:25]:
        if item.get("organisasjonsnummer") != ctx.org:
            continue
        words = describe_paths(item.get("endringer") or [])
        kind = item.get("endringstype")
        if kind == "Endring" and not words:
            continue
        date = (item.get("dato") or "")[:10]
        value = {"date": date, "type": {"Ny": "registered", "Endring": "updated", "Sletting": "deleted"}.get(kind, kind),
                 "changed": words, "update_id": item.get("oppdateringsid")}
        ev = ctx.add_evidence(snap, source_class=OFFICIAL_REGISTRY, method="brreg_update_feed_v1", licence=LICENCE,
                              span=json.dumps({"oppdateringsid": item.get("oppdateringsid"), "dato": item.get("dato"),
                                               "organisasjonsnummer": ctx.org, "endringstype": kind,
                                               **({"path": item["endringer"][0].get("path")} if item.get("endringer") else {})},
                                              ensure_ascii=False),
                              effective_date=date, anchor=("oppdateringsid", item.get("oppdateringsid")))
        ctx.add_claim("registry_change", "activity", value, ev, as_of=date, match=m)
