"""Prototype phrases per hazard class, in English / Hindi / Tamil (native + romanized).

The embedding classifier compares report text against these; a fine-tuned MuRIL
model can later replace this file + classifier without touching callers.
"""

PROTOTYPES: dict[str, list[str]] = {
    "coastal_flooding": [
        "sea water is entering the streets and houses are flooding",
        "the road near the beach is under water",
        "water level rising fast in our village near the coast",
        "समुद्र का पानी गलियों में घुस रहा है, घरों में बाढ़ आ गई है",
        "गांव में पानी भर गया है, सड़कें डूब गई हैं",
        "கடல் நீர் தெருக்களில் புகுந்து வீடுகளில் வெள்ளம்",
        "kadal thanni theruvukku vandhuruchu, veedu ellam vellam",
        "paani ghar mein ghus gaya hai, sadak doob gayi",
    ],
    "storm_surge": [
        "huge storm waves pushing sea water far inland",
        "cyclone surge flooding the coast, water came over the sea wall",
        "तूफान की लहरें समुद्र की दीवार के ऊपर से आ रही हैं",
        "புயல் அலைகள் கடல் சுவரை தாண்டி உள்ளே வருகிறது",
        "cyclone se samundar ka paani andar aa raha hai",
    ],
    "high_waves": [
        "very high waves crashing on the shore, bigger than normal",
        "dangerous swell, waves reaching the road",
        "बहुत ऊंची लहरें किनारे पर टकरा रही हैं",
        "மிக உயரமான அலைகள் கரையில் மோதுகின்றன",
        "romba periya alaigal, karaiyil adikkuthu",
        "waves bahut unchi hai aaj, kinare mat jao",
    ],
    "tsunami": [
        "the sea suddenly pulled back very far from the beach",
        "tsunami wave coming, water receded and now a huge wave",
        "समुद्र अचानक बहुत पीछे चला गया है",
        "கடல் திடீரென பின்வாங்கியது, சுனாமி அலை",
        "kadal pinnadi poiduchu, sunami varuthu",
    ],
    "rip_current": [
        "strong current pulling swimmers out to sea",
        "someone got dragged away from the beach by the current",
        "तेज़ धारा तैराकों को समुद्र में खींच रही है",
        "நீரோட்டம் நீச்சல் அடிப்பவர்களை உள்ளே இழுக்கிறது",
        "current romba strong, oruthar ulla izhuthutu ponga",
    ],
    "oil_spill": [
        "black oil floating on the sea surface near the harbour",
        "oil slick on the water, dead fish smell of diesel",
        "समुद्र की सतह पर काला तेल फैला हुआ है",
        "கடல் மேற்பரப்பில் எண்ணெய் படிந்துள்ளது",
        "thanni mela oil mathiri karuppa irukku, meen sethu pochu",
    ],
    "algal_bloom": [
        "the sea water turned green red and smells bad, many dead fish",
        "strange coloured water and fish kill along the beach",
        "समुद्र का पानी हरा लाल हो गया है, मछलियां मर रही हैं",
        "கடல் நீர் பச்சை சிவப்பு நிறமாக மாறியது, மீன்கள் இறக்கின்றன",
        "thanni colour maari pochu, meenu ellam mela mithakkuthu",
    ],
    "erosion": [
        "the beach sand is washing away, coastline eating into the land",
        "houses near the shore collapsing as the sea eats the beach",
        "समुद्र किनारे की रेत बह रही है, तट कट रहा है",
        "கடற்கரை மணல் அரிக்கப்படுகிறது, கரை உள்வாங்குகிறது",
        "beach kammiya pochu, kadal ulla vanthuruchu",
    ],
}

# Light keyword fallback (NLP_MODE=keyword or model unavailable).
KEYWORDS: dict[str, list[str]] = {
    "coastal_flooding": ["flood", "under water", "water entering", "बाढ़", "पानी भर", "வெள்ளம்", "vellam", "paani bhar"],
    "storm_surge": ["surge", "cyclone", "storm", "तूफान", "புயல்", "puyal", "toofan"],
    "high_waves": ["wave", "swell", "लहर", "அலை", "alai", "lehar"],
    "tsunami": ["tsunami", "sea pulled back", "receded", "सुनामी", "சுனாமி", "sunami"],
    "rip_current": ["rip", "current", "dragged", "धारा", "நீரோட்டம்", "izhu"],
    "oil_spill": ["oil", "slick", "diesel", "तेल", "எண்ணெய்", "ennai"],
    "algal_bloom": ["algae", "bloom", "dead fish", "red water", "green water", "मछलियां मर", "மீன்கள் இற", "meen sethu"],
    "erosion": ["erosion", "sand washing", "coastline", "कटाव", "அரிப்பு", "arippu"],
}

URGENCY_HIGH = [
    "trapped", "help", "emergency", "drowning", "rising fast", "collapsing", "dying",
    "बचाओ", "मदद", "फंसे", "डूब", "உதவி", "மூழ்கு", "சிக்கி",
    "kaapathunga", "udhavi", "bachao", "madad",
]
URGENCY_LOW = ["yesterday", "last week", "slowly", "small", "minor", "कल", "धीरे", "நேற்று", "மெதுவாக"]

# Secondhand-account markers (reported speech, not a firsthand observation).
HEARSAY_MARKERS = [
    "i heard", "someone said", "someone told me", "people are saying", "apparently",
    "according to", "they say", "rumor", "rumour", "heard that", "my friend said",
    "सुना है", "लोग कह रहे हैं", "कहा जा रहा है", "किसी ने बताया",
    "கேள்விப்பட்டேன்", "சொல்கிறார்கள்", "நண்பர் சொன்னார்", "கேள்விப்பட்டது",
]
