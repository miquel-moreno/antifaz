"""Identifiers written in odd ways must not reach the provider (OpenAI proxy + library)."""

# Look-alike characters are the attack itself.
# ruff: noqa: RUF001

import pytest
from fastapi.testclient import TestClient

from antifaz import mask
from tests.integration.fakes import FakeUpstream
from tests.redteam.conftest import DNI, IBAN, NIE, OPENAI_AUTH, PHONE, assert_not_sent, openai_body

# (text sent by the user, value that must not leave)
RESISTED = {
    "dni_minusculas": ("mi dni es 12345678z", DNI),
    "dni_puntos_guion": ("DNI 12.345.678-Z", DNI),
    "dni_guion": ("DNI 12345678-Z", DNI),
    "dni_en_url": ("https://x.test/?dni=12345678Z&t=1", DNI),
    "dni_en_nombre_fichero": ("adjunto informe_12345678Z.pdf", DNI),
    "dni_entre_guiones_bajos": ("id_12345678Z_final", DNI),
    "nie_minusculas": ("nie x1234567l", NIE),
    "nie_guiones": ("NIE X-1234567-L", NIE),
    "iban_espacios": ("ES91 2100 0418 4502 0005 1332", IBAN),
    "iban_minusculas": ("es91 2100 0418 4502 0005 1332", IBAN),
    "iban_guiones": ("ES91-2100-0418-4502-0005-1332", IBAN),
    "tel_espacios": ("tel 612 34 56 78", PHONE),
    "tel_puntos": ("tel 612.345.678", PHONE),
    "tel_guiones": ("tel 612-345-678", PHONE),
    "tel_prefijo": ("tel +34 612 345 678", PHONE),
    "tel_0034": ("tel 0034612345678", PHONE),
    "tel_parentesis": ("tel (+34) 612345678", PHONE),
}

# Leaks found in round 1 and fixed by the detector normalisation (ADR-0014).
LEAKS = {
    "dni_con_espacios": ("DNI 12 345 678 Z", DNI),
    "dni_partido_en_dos_lineas": ("DNI 1234\n5678Z", DNI),
    "iban_partido_en_dos_lineas": ("ES91 2100 0418\n4502 0005 1332", IBAN),
    "dni_pegado_a_la_etiqueta": ("DNI12345678Z", DNI),
    "dni_dentro_de_palabra": ("ref abc12345678Zxyz", DNI),
    "nie_pegado_a_la_etiqueta": ("NIEX1234567L", NIE),
    "dni_guion_blando": ("DNI 1234­5678Z", DNI),
    "dni_letra_cirilica": ("DNI 12345678З", "12345678"),
    "dni_letra_griega": ("DNI 12345678Ζ", "12345678"),
    "nie_x_cirilica": ("NIE Х1234567L", "1234567L"),
    "iban_e_cirilica": ("ЕS9121000418450200051332", "9121000418450200051332"),
}

# Invisible characters, odd spaces and full-width digits: normalised since ADR-0014.
UNICODE = {
    "dni_espacio_ancho_cero": ("DNI 1234​5678Z", DNI),
    "dni_espacio_duro": ("DNI 12 345 678 Z", DNI),
    "dni_tabuladores": ("DNI 12\t345\t678\tZ", DNI),
    "dni_cifras_ancho_completo": ("DNI １２３４５６７８Z", DNI),
}


def _attack(proxy: TestClient, upstream: FakeUpstream, text: str, value: str) -> None:
    response = proxy.post("/v1/chat/completions", json=openai_body(text), headers=OPENAI_AUTH)
    assert response.status_code in (200, 400)
    assert_not_sent(upstream, value)


@pytest.mark.parametrize(("text", "value"), RESISTED.values(), ids=RESISTED.keys())
def test_formato_raro_resistido(
    openai_proxy: TestClient, upstream_openai: FakeUpstream, text: str, value: str
) -> None:
    """El atacante escribe el dato con puntos, guiones, minúsculas o dentro de una URL."""
    _attack(openai_proxy, upstream_openai, text, value)


@pytest.mark.parametrize(("text", "value"), LEAKS.values(), ids=LEAKS.keys())
def test_formato_raro_fuga(
    openai_proxy: TestClient, upstream_openai: FakeUpstream, text: str, value: str
) -> None:
    """El atacante parte el dato, lo pega a otras letras o cambia una letra por otra idéntica."""
    _attack(openai_proxy, upstream_openai, text, value)


@pytest.mark.parametrize(("text", "value"), UNICODE.values(), ids=UNICODE.keys())
def test_formato_unicode_resistido(
    openai_proxy: TestClient, upstream_openai: FakeUpstream, text: str, value: str
) -> None:
    """El atacante mete espacios invisibles, espacios duros o cifras de ancho completo."""
    _attack(openai_proxy, upstream_openai, text, value)


def test_libreria_mask_no_deja_el_dato_en_el_texto() -> None:
    """Usando la librería directamente, el texto enmascarado no conserva el DNI."""
    result = mask(f"Hola, soy {DNI} y mi IBAN es {IBAN}, tel {PHONE}")
    for value in (DNI, IBAN, PHONE):
        assert value not in result.text


# Round 2 (review): combining marks, variation selectors, a lone CR and Hangul fillers.
ROUND_2 = {
    "dni_selector_de_variante": ("DNI 1234\ufe0f5678Z", DNI),
    "dni_acento_combinante": ("DNI 1234\u03015678Z", DNI),
    "dni_retorno_de_carro": ("DNI 1234\r5678Z", DNI),
    "dni_relleno_hangul": ("DNI 1234\u31645678Z", DNI),
    "dni_acento_en_la_primera_cifra": ("1\u03012345678Z", DNI),
    "dni_selector_suplementario": ("DNI 1234\U000e01005678Z", DNI),
}


@pytest.mark.parametrize(("text", "value"), ROUND_2.values(), ids=ROUND_2.keys())
def test_formato_raro_ronda_2(
    openai_proxy: TestClient, upstream_openai: FakeUpstream, text: str, value: str
) -> None:
    """El atacante mete marcas combinantes, selectores de variante, CR o rellenos hangul."""
    _attack(openai_proxy, upstream_openai, text, value)
