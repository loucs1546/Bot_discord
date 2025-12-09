# config/filters.py

# === LISTE BLANCHE (évite les faux positifs) ===
WHITELISTED_PHRASES = {
    "...", "att", "f", "nan", "?", "quoi", "quoi xd", "woaaaaaaaaaaaaaaaa",
    "ddddd", "ok", "oui", "non", "stp", "s'il te plaît", "merci", "hello",
    "salut", "bonjour", "aide", "help", "hein", "wtf", "lol", "mdr", "ah",
    "ouais", "ouai", "bah", "ben", "euh", "hm", "hmm", "bon", "allez", "XD",
    "xD", "xd"
}

# === MOTS INTERDITS (à compléter avec ta liste) ===
MOTS_INTERDITS = {
    "connard",
    "conne",
    "pute",
    "salope",
    "enculé",
    "bordel",
    "fuck",
    "shit",
    "bitch",
    "asshole",
    "dick",
    "pénis",
    "vagin",
    "porn",
    "porno",
    "sex",
    "fuckyou",
    "bastard",
    "motherfucker",
    "nazi",
    "raciste",
    "homophobe",
    "sodomie",
    "masturbation",
    "cunnilingus",
    "fellation",
    "inceste",
    "violer",
    "viol",
    "merdal",
    "batard",
    "salope",
    "garce",
    "fils de pute",
    "pute de luxe",
    "tarlouze",
    "tapette",
    "pd",
    "enculée",
    "gouine",
    "trou du cul",
    "gros con",
    "gros cul",
    "nique",
    "ta gueule",
    "nique ta mère",
    "nique le système",
    "putes",
    "casse-toi",
    "pute borgne",
    "salope de merde",
    "conasse",
    "connasses",
    "cul",
    "bite",
    "chatte",
    "couilles",
    "sexe",
    "pornographie",
    "masturber",
    "orgie",
    "orgies",
    "striptease",
    "prostituée",
    "escort",
    "viols",
    "abusive",
    "t'es chiant",
    "mange tes morts",
    "tg",
    "fdp",
    "ta gueule",
    "sale chien",
    "sale merde"
}
# === MOTS INTERDITS DANS LES URLS ===
MOTS_INTERDITS_URL = {
    "pornhub",
    "youporn",
    "xvideos",
    "xhamster",
    "redtube",
    "xnxx",
    "tube8",
    "spankwire",
    "porn.com",
    "fap",
    "brazzers",
    "naughtyamerica",
    "bangbros",
    "javhub",
    "eroprofile",
    "xart",
    "pornhd",
}

def est_contenu_suspect(contenu: str) -> bool:
    if not contenu.strip():
        return False
    contenu_min = contenu.lower().strip()

    # Liste blanche
    if contenu_min in WHITELISTED_PHRASES:
        return False

    # Mots interdits
    for mot in MOTS_INTERDITS:
        if mot in contenu_min:
            return True

    # Trop de majuscules
    if len(contenu) > 5:
        majuscules = sum(1 for c in contenu if c.isupper())
        if majuscules / len(contenu) > 0.7:
            return True

    # Trop répétitif (ex: "aaaaaaa")
    if any(contenu.count(c) / len(contenu) > 0.6 for c in set(contenu)):
        return True

    return False

def est_url_suspecte(url: str) -> bool:
    url_min = url.lower()
    for mot in MOTS_INTERDITS_URL:
        if mot in url_min:
            return True
    return False