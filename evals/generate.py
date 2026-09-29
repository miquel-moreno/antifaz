"""Synthetic Antifaz-Bench dataset: texts with ground truth recorded at construction time.

Labels are Antifaz EntityType values. Identifiers are built with their own check-digit
algorithms from a seeded random.Random; names, streets and companies come from small
invented lists (no Faker: fully controlled output and no extra dependency). Ground truth
is recorded when each value is inserted, never by running the detector. CIFs are always
annotated as ES_CIF: a CIF identifies a legal person, but it can also point to a sole
trader. Person names are not annotated yet (there is no PERSON type before the NER).

Bias, declared: the same team wrote this generator and the detector, so results on this
set are likely better than on real text. MEDDOCAN (written by third parties) comes first.

    uv run python -m evals.generate     # writes evals/datasets/synthetic-v1.jsonl
"""

import argparse
import json
import random
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from antifaz.detect.types import EntityType
from antifaz.detect.validators import ccc, dni, luhn, nss
from evals.datasets.meddocan import Document
from evals.metrics import Annotation

KINDS: Mapping[str, int] = {
    "email": 100,
    "ticket": 80,
    "payslip": 60,
    "contract": 60,
    "chat": 80,
    "csv": 60,
    "code": 60,
    "catalan": 60,
    "trap": 40,
}
DATASET = Path(__file__).resolve().parent / "datasets" / "synthetic-v1.jsonl"

T = EntityType
FIRST_NAMES = [
    "Ana",
    "Marta",
    "Lucía",
    "Pablo",
    "Jordi",
    "Nerea",
    "Iker",
    "Carmen",
    "Raúl",
    "Elena",
]
SURNAMES = ["Prueba", "Ejemplo", "Ficticio", "Inventado", "Muestra", "Demo", "Simulado"]
STREETS = [
    ("C/", "Mayor"),
    ("Calle", "de la Paz"),
    ("Avda.", "Ejemplo"),
    ("Plaza", "Mayor"),
    ("Paseo", "de las Pruebas"),
    ("Calle", "Pau Casals"),  # a street named after a person: an address, not a name
    ("Carrer", "de la Mostra"),
    ("Ronda", "de Sant Ficticio"),
]
COMPANIES = ["Recambios Demo SL", "Talleres Ejemplo SA", "Muestra Logística SL", "Prueba Soft SA"]
DOMAINS = ["example.com", "example.org", "example.net", "mail.example.com"]
MONTH_NAMES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
               "septiembre", "octubre", "noviembre", "diciembre"]  # fmt: skip


def _rng(seed: int) -> random.Random:
    return random.Random(seed)  # noqa: S311 - synthetic test data, not security


def _digits(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("0123456789") for _ in range(n))


# --- Identifier builders -------------------------------------------------------------------


def make_dni(rng: random.Random) -> str:
    number = _digits(rng, 8)
    letter = dni.control_letter(number)
    style = rng.randrange(3)
    if style == 0:
        return number + letter
    if style == 1:
        return f"{number}-{letter}"
    return f"{number[:2]}.{number[2:5]}.{number[5:]}-{letter}"


def make_nie(rng: random.Random) -> str:
    prefix = rng.choice("XYZ")
    body = _digits(rng, 7)
    letter = dni.control_letter(str("XYZ".index(prefix)) + body)
    return f"{prefix}{body}{letter}" if rng.random() < 0.7 else f"{prefix}-{body}-{letter}"


def make_nif_klm(rng: random.Random) -> str:
    body = _digits(rng, 7)
    return rng.choice("KLM") + body + dni.control_letter(body)


def make_cif(rng: random.Random) -> str:
    letter = rng.choice("ABCDEFGHJNPQRSUVW")
    body = _digits(rng, 7)
    check = luhn.check_digit(body)
    control = "JABCDEFGHI"[check] if letter in "PQRSNW" else str(check)
    return letter + body + control


def make_nss(rng: random.Random) -> str:
    province = rng.randint(1, 52)
    number = rng.randrange(10**8)
    control = nss.control(province, number)
    parts = (f"{province:02d}", f"{number:08d}", f"{control:02d}")
    return rng.choice([" ", "/", ""]).join(parts)


def _ccc_digits(rng: random.Random) -> str:
    entity, office, account = _digits(rng, 4), _digits(rng, 4), _digits(rng, 10)
    controls = ccc.control_digit("00" + entity + office) + ccc.control_digit(account)
    return entity + office + controls + account


def make_ccc(rng: random.Random) -> str:
    digits = _ccc_digits(rng)
    if rng.random() < 0.5:
        return digits
    return f"{digits[:4]} {digits[4:8]} {digits[8:10]} {digits[10:]}"


def make_iban_es(rng: random.Random) -> str:
    bban = _ccc_digits(rng)
    rearranged = bban + "142800"  # "ES00" as digits: E=14, S=28
    check = 98 - int(rearranged) % 97
    iban = f"ES{check:02d}{bban}"
    if rng.random() < 0.5:
        return iban
    return " ".join(iban[i : i + 4] for i in range(0, len(iban), 4))


def make_card(rng: random.Random) -> str:
    prefix = rng.choice(["4", "51", "55", "37", "6011"])
    length = 15 if prefix == "37" else 16
    body = prefix + _digits(rng, length - len(prefix) - 1)
    number = body + str(luhn.check_digit(body))
    if rng.random() < 0.5:
        return number
    if length == 15:
        return f"{number[:4]} {number[4:10]} {number[10:]}"
    return " ".join(number[i : i + 4] for i in range(0, 16, 4))


def make_phone(rng: random.Random) -> str:
    first = rng.choice(["6", "7", "91", "93", "96"])
    number = first + _digits(rng, 9 - len(first))
    style = rng.randrange(4)
    if style == 0:
        return number
    if style == 1:
        return f"{number[:3]} {number[3:6]} {number[6:]}"
    if style == 2:
        return f"+34 {number[:3]} {number[3:5]} {number[5:7]} {number[7:]}"
    return f"0034 {number}"


def _person(rng: random.Random) -> tuple[str, str]:
    return rng.choice(FIRST_NAMES), rng.choice(SURNAMES)


def make_email(rng: random.Random) -> str:
    first, last = _person(rng)
    local = f"{first}.{last}".lower().replace("í", "i").replace("ú", "u")
    return f"{local}{rng.randint(1, 99)}@{rng.choice(DOMAINS)}"


def _address(rng: random.Random) -> str:
    kind, name = rng.choice(STREETS)
    text = f"{kind} {name} {rng.randint(1, 120)}"
    if rng.random() < 0.5:
        text += f", {rng.randint(1, 9)}º {rng.choice('ABCD')}"
    if rng.random() < 0.5:
        text += f", {rng.randint(1, 52):02d}{_digits(rng, 3)}"
    return text


def _birth_date(rng: random.Random) -> str:
    day, month, year = rng.randint(1, 28), rng.randint(1, 12), rng.randint(1940, 2005)
    if rng.random() < 0.7:
        return f"{day:02d}/{month:02d}/{year}"
    return f"{day} de {MONTH_NAMES[month - 1]} de {year}"


def _passport(rng: random.Random) -> str:
    return "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ") for _ in range(3)) + _digits(rng, 6)


def _plate(rng: random.Random) -> str:
    return _digits(rng, 4) + " " + "".join(rng.choice("BCDFGHJKLMNPRSTVWXYZ") for _ in range(3))


def _ip(rng: random.Random) -> str:
    return f"192.0.2.{rng.randint(1, 254)}"  # RFC 5737 documentation range


def _wrong_dni(rng: random.Random) -> str:
    number = _digits(rng, 8)
    right = dni.control_letter(number)
    wrong = rng.choice([c for c in dni.LETTERS if c != right])
    return f"{number[:2]}.{number[2:5]}.{number[5:]}-{wrong}"


# --- Texts -----------------------------------------------------------------------------------


class _Builder:
    """Concatenates text and records the span of every inserted value."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.length = 0
        self.annotations: list[Annotation] = []

    def text(self, value: str) -> "_Builder":
        self.parts.append(value)
        self.length += len(value)
        return self

    def value(self, value: str, entity_type: EntityType) -> "_Builder":
        self.annotations.append(
            Annotation(self.length, self.length + len(value), entity_type.value)
        )
        return self.text(value)

    def build(self, name: str) -> Document:
        return Document(name, "".join(self.parts), tuple(self.annotations))


def _email_text(rng: random.Random, b: _Builder) -> None:
    first, last = _person(rng)
    b.text(f"Hola,\n\nSoy {first} {last}. Os escribo por el pedido de la semana pasada. ")
    b.text("Mi DNI es ").value(make_dni(rng), T.ES_DNI).text(" y podéis llamarme al ")
    b.value(make_phone(rng), T.PHONE).text(".\nEnviad la factura a ")
    b.value(make_email(rng), T.EMAIL).text(".\n\nGracias,\n" + first)


def _ticket_text(rng: random.Random, b: _Builder) -> None:
    b.text(f"Ticket #{rng.randint(1000, 9999)}: el cliente no recibe el envío en ")
    b.value(_address(rng), T.ADDRESS).text(". Teléfono de contacto: ")
    b.value(make_phone(rng), T.PHONE).text(". Correo: ").value(make_email(rng), T.EMAIL)
    b.text(".")


def _payslip_text(rng: random.Random, b: _Builder) -> None:
    first, last = _person(rng)
    b.text(f"NÓMINA · Empresa: {rng.choice(COMPANIES)} (CIF ")
    b.value(make_cif(rng), T.ES_CIF).text(")\nTrabajador: " + f"{first} {last}\nNIF: ")
    b.value(make_dni(rng), T.ES_DNI).text("\nNº afiliación SS: ").value(make_nss(rng), T.ES_NSS)
    b.text("\nIngreso en cuenta: ").value(make_iban_es(rng), T.IBAN)
    b.text(f"\nLíquido a percibir: {rng.randint(900, 3500)},00 €")


def _contract_text(rng: random.Random, b: _Builder) -> None:
    first, last = _person(rng)
    b.text("CONTRATO DE ARRENDAMIENTO\n\nDe una parte, " + f"{first} {last}, con NIE ")
    b.value(make_nie(rng), T.ES_NIE).text(", fecha de nacimiento ")
    b.value(_birth_date(rng), T.DATE_OF_BIRTH).text(", y domicilio en ")
    b.value(_address(rng), T.ADDRESS).text(".\nEl pago se hará en la cuenta ")
    b.value(make_ccc(rng), T.ES_CCC).text(".")


def _chat_text(rng: random.Random, b: _Builder) -> None:
    b.text("[10:02] cliente: hola, me pasáis el estado del pedido?\n[10:03] agente: claro, ")
    b.text("¿me confirmas tu móvil?\n[10:04] cliente: ").value(make_phone(rng), T.PHONE)
    b.text("\n[10:05] cliente: y pagué con la tarjeta ").value(make_card(rng), T.CREDIT_CARD)
    b.text("\n[10:06] agente: gracias, lo reviso")


def _csv_text(rng: random.Random, b: _Builder) -> None:
    b.text("nombre;dni;telefono;email\n")
    for _ in range(3):
        first, last = _person(rng)
        b.text(f"{first} {last};").value(make_dni(rng), T.ES_DNI).text(";")
        b.value(make_phone(rng), T.PHONE).text(";").value(make_email(rng), T.EMAIL).text("\n")


def _code_text(rng: random.Random, b: _Builder) -> None:
    b.text('logger.info("login ok", extra={"user_email": "')
    b.value(make_email(rng), T.EMAIL).text('", "ip": "').value(_ip(rng), T.IP).text('"})\n')
    b.text('payment = {"iban": "').value(make_iban_es(rng), T.IBAN).text('", "amount": 120}\n')


def _catalan_text(rng: random.Random, b: _Builder) -> None:
    first, _ = _person(rng)
    b.text(f"Bon dia,\n\nSóc {first}. El meu DNI és ").value(make_dni(rng), T.ES_DNI)
    b.text(" i visc al ").value(f"Carrer de la Mostra {rng.randint(1, 90)}", T.ADDRESS)
    b.text(". El meu passaport: pasaporte ").value(_passport(rng), T.ES_PASSPORT)
    b.text(".\nLa matrícula del cotxe: matrícula ").value(_plate(rng), T.ES_PLATE)
    b.text(".\nSalutacions")


def _trap_text(rng: random.Random, b: _Builder) -> None:
    # Values that look personal but are not: nothing here may be annotated except the
    # genuine address and the NIF K/L/M.
    b.text(f"Pedido {_digits(rng, 8)} con referencia REF-{_digits(rng, 6)}. ")
    b.text("Número que parece un DNI pero no lo es: " + _wrong_dni(rng) + ". ")
    b.text(f"Fecha de la factura: {rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/2025. ")
    b.text("Recoger en ").value(_address(rng), T.ADDRESS).text(". NIF del titular: ")
    b.value(make_nif_klm(rng), T.ES_NIF).text(".")


_TEXTS: Mapping[str, Callable[[random.Random, _Builder], None]] = {
    "email": _email_text,
    "ticket": _ticket_text,
    "payslip": _payslip_text,
    "contract": _contract_text,
    "chat": _chat_text,
    "csv": _csv_text,
    "code": _code_text,
    "catalan": _catalan_text,
    "trap": _trap_text,
}


def generate(seed: int = 7, counts: Mapping[str, int] = KINDS) -> list[Document]:
    """Documents of every kind, in a fixed order; the same seed gives the same output."""
    rng = _rng(seed)
    documents = []
    for kind, count in counts.items():
        for i in range(1, count + 1):
            builder = _Builder()
            _TEXTS[kind](rng, builder)
            documents.append(builder.build(f"{kind}-{i:03d}"))
    return documents


def to_jsonl(documents: Iterable[Document]) -> str:
    lines = [
        json.dumps(
            {
                "name": d.name,
                "text": d.text,
                "annotations": [[a.start, a.end, a.label] for a in d.annotations],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        for d in documents
    ]
    return "\n".join(lines) + "\n"


def from_jsonl(text: str) -> list[Document]:
    documents = []
    for line in text.splitlines():
        if line.strip():
            row = json.loads(line)
            annotations = tuple(Annotation(s, e, label) for s, e, label in row["annotations"])
            documents.append(Document(row["name"], row["text"], annotations))
    return documents


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the synthetic Antifaz-Bench dataset.")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(list(argv) if argv is not None else None)
    DATASET.write_text(to_jsonl(generate(args.seed)), encoding="utf-8", newline="\n")
    print(f"{DATASET.name}: {sum(KINDS.values())} documents")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
