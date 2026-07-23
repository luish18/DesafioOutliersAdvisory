"""Testes dos utilitários de leitura e reparo do loader."""

from loader import _alias, decode_mixed_encoding


def test_decode_mixed_encoding_repara_utf8_embutido():
    """Caso real: FIDC Stone IV, competência 2024-04.

    A denominação social vem em latin-1 (``\xc3`` = Ã) e o rótulo da
    subclasse, no mesmo registro, vem em UTF-8 cru (``\xc3\x83`` = Ã).
    """
    linha = b"CART\xc3O;Subclasse ... CART\xc3\x83O DE CR\xc3\x89DITO"
    assert decode_mixed_encoding(linha) == "CARTÃO;Subclasse ... CARTÃO DE CRÉDITO"


def test_decode_mixed_encoding_preserva_latin1_puro():
    assert decode_mixed_encoding(b"CR\xc9DITO") == "CRÉDITO"


def test_decode_mixed_encoding_nao_estaciona_no_fim_do_buffer():
    """Byte alto truncado no fim não pode travar o laço."""
    assert decode_mixed_encoding(b"abc\xc3") == "abcÃ"


def test_alias_faz_coalesce_quando_ha_dois_layouts():
    """Com histórico longo o corpus tem as duas colunas de CNPJ.

    Escolher só uma faria as linhas do outro layout virarem NULL e serem
    descartadas em silêncio — foi um bug real.
    """
    disponiveis = {"CNPJ_FUNDO_CLASSE", "CNPJ_FUNDO", "DT_COMPTC"}
    assert _alias(disponiveis, "cnpj") == "coalesce(CNPJ_FUNDO_CLASSE, CNPJ_FUNDO)"


def test_alias_com_layout_unico_nao_usa_coalesce():
    assert _alias({"CNPJ_FUNDO_CLASSE"}, "cnpj") == "CNPJ_FUNDO_CLASSE"


def test_alias_cai_no_fallback_quando_coluna_nao_existe():
    assert _alias({"DT_COMPTC"}, "tp_fundo_classe", "'FUNDO'") == "'FUNDO'"
