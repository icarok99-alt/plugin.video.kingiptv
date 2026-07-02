import re
import unicodedata
from difflib import SequenceMatcher
_NOISE_WORDS = {
    'dublado', 'dub', 'legendado', 'leg', 'nacional', 'dual', 'audio',
    'hd', 'fhd', 'uhd', 'sd', '4k', '8k', 'hdr',
    'cam', 'ts', 'web', 'webrip', 'web-dl', 'webdl', 'bluray', 'blu', 'ray',
    'brrip', 'bdrip', 'dvdrip', 'dvd', 'remux', 'rip',
    'multi', 'original', 'extended', 'directors', 'cut', 'uncut',
    'temporada', 'completa', 'serie', 'series',
}
_YEAR_RE = re.compile(r'\b((19|20)\d{2})\b')
# Palavras "vazias" (artigos, preposições, conjunções) que não devem contar
# como sinal de semelhança entre títulos. Sem isso, títulos totalmente
# diferentes como "Mestres do Universo" e "Mestres do Assalto" ganham score
# alto só por compartilharem "mestres" + "do".
_STOPWORDS = {
    'a', 'o', 'as', 'os', 'um', 'uma', 'uns', 'umas',
    'de', 'do', 'da', 'dos', 'das', 'em', 'no', 'na', 'nos', 'nas',
    'por', 'pra', 'para', 'com', 'sem', 'e', 'ou',
    'the', 'of', 'in', 'on', 'at', 'to', 'and', 'or',
}

def _strip_accents(text):
    text = unicodedata.normalize('NFKD', text)
    return ''.join(ch for ch in text if not unicodedata.combining(ch))

def normalize_title(text):
    if not text:
        return ''
    text = _strip_accents(str(text)).lower()
    text = re.sub(r'\[[^\]]*\]', ' ', text)
    text = re.sub(r'\([^)]*\)', ' ', text)
    text = _YEAR_RE.sub(' ', text)
    text = re.sub(r'[^a-z0-9 ]+', ' ', text)
    words = [w for w in text.split() if w and w not in _NOISE_WORDS]
    return ' '.join(words).strip()

def extract_year(text):
    if not text:
        return None
    m = _YEAR_RE.search(str(text))
    return m.group(1) if m else None

def _word_overlap_score(sa, sb):
    inter = sa & sb
    if not inter:
        return 0.0
    union = sa | sb
    jaccard = len(inter) / len(union)
    containment = len(inter) / min(len(sa), len(sb))
    return (jaccard + containment) / 2.0

def title_similarity(a, b):
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta = na.split()
    tb = nb.split()
    # Remove stopwords antes de medir semelhança, senão palavras como "do"/
    # "de"/"the" contam como "match" e mascaram títulos diferentes que só
    # coincidem numa palavra comum + uma preposição.
    fa = [w for w in ta if w not in _STOPWORDS]
    fb = [w for w in tb if w not in _STOPWORDS]
    if fa and fb:
        ta, tb = fa, fb
    sa, sb = set(ta), set(tb)
    if not (sa & sb):
        return 0.0
    if min(len(sa), len(sb)) <= 1:
        return 0.0
    seq_score = SequenceMatcher(None, ta, tb, autojunk=False).ratio()
    word_score = _word_overlap_score(sa, sb)
    return (seq_score * 0.6) + (word_score * 0.4)

def best_title_match(candidates, name_key, titles, year=None,
                      min_score=0.62, year_bonus=0.08, year_key='year',
                      ambiguous_margin=0.05, year_tolerance=1,
                      unknown_year_min_score=0.82, debug_log=None):
    best = None
    best_score = 0.0
    best_required = min_score
    second_score = 0.0
    second_name = None
    titles = [t for t in titles if t]
    if not titles:
        return None, 0.0
    try:
        target_year = int(str(year)[:4]) if year else None
    except ValueError:
        target_year = None
    for item in candidates:
        if not isinstance(item, dict):
            continue
        candidate_name = item.get(name_key, '')
        if not candidate_name:
            continue
        score = 0.0
        for t in titles:
            s = title_similarity(t, candidate_name)
            if s > score:
                score = s
        raw_score = score
        required = min_score if target_year else unknown_year_min_score
        if target_year:
            cand_year_raw = item.get(year_key) or extract_year(candidate_name)
            try:
                cand_year = int(str(cand_year_raw)[:4]) if cand_year_raw else None
            except ValueError:
                cand_year = None
            if cand_year:
                year_matches = abs(cand_year - target_year) <= year_tolerance
            else:
                # candidato sem ano conhecido: não zera por ano, mas passa a
                # exigir um título muito mais forte (unknown_year_min_score)
                required = unknown_year_min_score
                year_matches = True
            # O ano nunca pode resgatar um título fraco: o bônus só é somado
            # se o título já bateu sozinho (raw_score >= required). Se o
            # título OU o ano não baterem, zera na hora.
            title_matches = raw_score >= required
            if title_matches and year_matches:
                score = min(1.0, raw_score + year_bonus) if cand_year else raw_score
            else:
                score = 0.0
        if score > best_score:
            second_score = best_score
            second_name = best.get(name_key) if best else None
            best_score = score
            best = item
            best_required = required
        elif score > second_score:
            second_score = score
            second_name = candidate_name
    if best is None or best_score < best_required:
        if debug_log:
            debug_log('[matcher] REJEITADO: melhor={!r} score={:.3f} < exigido={:.3f}'.format(
                best.get(name_key) if best else None, best_score, best_required
            ))
        return None, best_score
    if best_score < 0.85 and (best_score - second_score) < ambiguous_margin:
        if debug_log:
            debug_log('[matcher] REJEITADO por ambiguidade: melhor={!r} score={:.3f} vs 2o={!r} score={:.3f}'.format(
                best.get(name_key), best_score, second_name, second_score
            ))
        return None, best_score
    if debug_log:
        debug_log('[matcher] ACEITO: {!r} score={:.3f}'.format(best.get(name_key), best_score))
    return best, best_score
