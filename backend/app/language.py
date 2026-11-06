"""Which language the buyer writes in, and the agent's fixed messages in that language.

The agent writes replies with a model, so it is told which language to use. Everything the code itself says (the option layout, the
questions it asks, the error messages, the button labels) comes from this catalogue, so an Amharic-speaking buyer is never answered in
English half way through. Amharic here is written by the developer, not a translation service: have a native speaker review it.
Button *texts* stay in English on purpose: they are the fixed sentences the agent recognises in code; only the labels are translated.
"""
from __future__ import annotations

ETHIOPIC = range(0x1200, 0x139E)  # the Ethiopic block, which Amharic is written in
AMHARIC_SHARE = 0.3


def detect_language(text: str) -> str:
    """'am' when at least 30% of the letters are Ethiopic, otherwise 'en'. A mixed message follows its dominant script."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "en"
    return "am" if sum(ord(c) in ETHIOPIC for c in letters) / len(letters) >= AMHARIC_SHARE else "en"


PERSONA_ADDENDUM = {
    "en": "",
    "am": " The buyer writes in Amharic. Write every reply in Amharic (አማርኛ) using Ethiopic script. Keep numbers, prices (ETB) and unit names as given.",
}

MESSAGES: dict[str, dict[str, str]] = {
    "unsupported": {
        "en": "I can read text and voice messages. What kind of home are you looking for (budget, bedrooms, area)?",
        "am": "ጽሑፍና የድምፅ መልዕክት ማንበብ እችላለሁ። ምን ዓይነት ቤት ይፈልጋሉ (በጀት፣ የመኝታ ክፍል ብዛት፣ አካባቢ)?"},
    "trouble": {
        "en": "I'm having trouble right now. Please send that again in a moment.",
        "am": "አሁን ችግር አጋጥሞኛል። እባክዎ ትንሽ ቆይተው መልዕክትዎን እንደገና ይላኩ።"},
    "quota_reached": {
        "en": "This agency's assistant has reached its daily limit of conversations. Your message has been saved and the broker will reply personally.",
        "am": "የዚህ ኤጀንሲ ረዳት የዕለቱን የውይይት ገደብ አልፏል። መልዕክትዎ ተቀምጧል፤ ደላላው በግል ይመልስልዎታል።"},
    "budget_reached": {
        "en": "This agency's assistant has reached its budget for this month. Your message has been saved and the broker will reply personally.",
        "am": "የዚህ ኤጀንሲ ረዳት የወሩን በጀት አልፏል። መልዕክትዎ ተቀምጧል፤ ደላላው በግል ይመልስልዎታል።"},
    "calendar_down": {
        "en": "I can't reach the broker's calendar right now, so I haven't booked anything. Please try again in a few minutes.",
        "am": "የደላላውን የቀን መቁጠሪያ አሁን ማግኘት አልቻልኩም፣ ስለዚህ ምንም አልያዝኩም። እባክዎ ከጥቂት ደቂቃዎች በኋላ ይሞክሩ።"},
    "voice_fallback": {
        "en": "I had trouble hearing the exact details in the audio. Could you please type out your preferred location and budget?",
        "am": "የድምፅ መልዕክቱን በትክክል መስማት አልቻልኩም። እባክዎ የሚፈልጉትን አካባቢና በጀት በጽሑፍ ይላኩልኝ።"},
    "which_option_details": {
        "en": "Which option would you like to know more about? Reply with its number.",
        "am": "ስለ የትኛው አማራጭ ተጨማሪ ማወቅ ይፈልጋሉ? ቁጥሩን ይላኩ።"},
    "option_gone": {
        "en": "That option is no longer available. Tell me what you're looking for and I'll search again.",
        "am": "ያ አማራጭ አሁን የለም። የሚፈልጉትን ንገሩኝና እንደገና እፈልጋለሁ።"},
    "nothing_to_compare": {
        "en": "I don't have any options to compare yet. Tell me your budget, bedrooms and preferred area and I'll find some.",
        "am": "ለማወዳደር እስካሁን ምንም አማራጭ የለኝም። በጀትዎን፣ የመኝታ ክፍል ብዛትና የሚመርጡትን አካባቢ ንገሩኝና አማራጮችን አገኛለሁ።"},
    "find_first": {
        "en": "Let me find a few options first. What are you looking for (budget, bedrooms, area)?",
        "am": "መጀመሪያ ጥቂት አማራጮችን ላግኝ። ምን ይፈልጋሉ (በጀት፣ የመኝታ ክፍል፣ አካባቢ)?"},
    "which_to_visit": {
        "en": "Which of the options would you like to visit? Reply with the numbers, for example 1 and 3.",
        "am": "ከአማራጮቹ የትኞቹን መጎብኘት ይፈልጋሉ? ቁጥሮቹን ይላኩ፣ ለምሳሌ 1 እና 3።"},
    "ask_contact": {
        "en": "To register your visit with the developer I need your {missing}. Could you share it?",
        "am": "ጉብኝትዎን ከአልሚው ጋር ለማስመዝገብ {missing} ያስፈልገኛል። ሊያጋሩኝ ይችላሉ?"},
    "ask_contact_call": {
        "en": "To arrange the call I need your {missing}. Could you share it?",
        "am": "ጥሪውን ለማመቻቸት {missing} ያስፈልገኛል። ሊያጋሩኝ ይችላሉ?"},
    "full_name": {"en": "full name", "am": "ሙሉ ስምዎን"},
    "phone_number": {"en": "phone number", "am": "ስልክ ቁጥርዎን"},
    "and": {"en": " and ", "am": " እና "},
    "waitlist": {
        "en": "We don't have a unit matching those criteria right now. I've added you to the Priority Watchlist and I'll message you the moment a match appears.",
        "am": "እነዚህን መስፈርቶች የሚያሟላ ቤት አሁን የለንም። በቅድሚያ የመጠባበቂያ ዝርዝር ውስጥ አስገብቼዎታለሁ፤ የሚመጥን ቤት እንደመጣ ወዲያውኑ እነግርዎታለሁ።"},
    "waitlist_area": {
        "en": "I don't have anything in {area} right now, and I won't offer other areas unless you ask. We are working in Addis Ababa at the moment, with the most choice in {areas}. I've added you to the Priority Watchlist for {area} and I'll message you the moment one appears. If you'd consider somewhere else, just tell me.",
        "am": "በ{area} አሁን ምንም ቤት የለኝም፤ እርስዎ ካልጠየቁ ሌላ አካባቢ አላቀርብም። በአሁኑ ጊዜ የምንሠራው በአዲስ አበባ ነው፤ ብዙ ምርጫ ያለው በ{areas} ነው። ለ{area} በቅድሚያ የመጠባበቂያ ዝርዝር ውስጥ አስገብቼዎታለሁ፤ ቤት እንደመጣ ወዲያውኑ እነግርዎታለሁ። ሌላ አካባቢ ቢያስቡ ብቻ ይንገሩኝ።"},
    "waitlist_budget": {
        "en": "I don't have anything within {budget} that matches right now, and I won't go above it unless you ask. I've added you to the Priority Watchlist and I'll message you the moment one appears. If you can stretch a little, just tell me.",
        "am": "ከ{budget} በታች የሚመጥን ቤት አሁን የለኝም፤ እርስዎ ካልጠየቁ ከዚህ በላይ አላቀርብም። በቅድሚያ የመጠባበቂያ ዝርዝር ውስጥ አስገብቼዎታለሁ፤ ቤት እንደመጣ ወዲያውኑ እነግርዎታለሁ። ጥቂት ማስተካከል ከቻሉ ብቻ ይንገሩኝ።"},
    "waitlist_buffer": {
        "en": "We don't have units matching those constraints even with a {pct}% buffer. I've added you to the Priority Watchlist and I'll message you the moment a match appears.",
        "am": "እስከ {pct}% ተጨማሪ በጀት ቢኖርም እነዚህን መስፈርቶች የሚያሟላ ቤት የለንም። በቅድሚያ የመጠባበቂያ ዝርዝር ውስጥ አስገብቼዎታለሁ፤ የሚመጥን ቤት እንደመጣ ወዲያውኑ እነግርዎታለሁ።"},
    "handoff": {
        "en": "Of course. I'm bringing {broker} into this chat now, and they will reply to you personally.",
        "am": "እሺ። {broker}ን አሁኑኑ ወደዚህ ውይይት እያስገባሁ ነው፤ እሳቸው ራሳቸው ይመልሱልዎታል።"},
    "broker_has_chat": {
        "en": "Thanks, I have passed your message to {broker}. They have this chat now and will reply to you personally.",
        "am": "እሺ፣ መልእክትዎን ለ{broker} አስተላልፌያለሁ። ውይይቱን አሁን ተረክበዋል፤ እሳቸው ራሳቸው ይመልሱልዎታል።"},
    "off_limits": {
        "en": "I can help with finding a home, comparing options and booking a visit. What are you looking for (budget, bedrooms, area)?",
        "am": "ቤት ለማግኘት፣ አማራጮችን ለማወዳደርና ጉብኝት ለመያዝ እረዳለሁ። ምን ይፈልጋሉ (በጀት፣ የመኝታ ክፍል፣ አካባቢ)?"},
    "down": {"en": "{pct}% down ({amount})", "am": "{pct}% ቅድመ ክፍያ ({amount})"},
    "delivery": {"en": "delivery {when}", "am": "ርክክብ {when}"},
    "afford_need_budget": {
        "en": "Tell me what monthly amount you can comfortably pay, and I'll check which of these units fit.",
        "am": "በየወሩ በምቾት ሊከፍሉት የሚችሉትን መጠን ንገሩኝና ከእነዚህ ውስጥ የትኞቹ እንደሚስማሙ እመለከታለሁ።"},
    "afford_intro": {
        "en": "Here is how each option fits a monthly budget of {budget}. These are estimates from the developers' advertised terms, not financial advice.",
        "am": "እያንዳንዱ አማራጭ በወር {budget} በጀት ላይ እንዴት እንደሚስማማ እነሆ። እነዚህ በአልሚዎቹ ከተገለጹ ውሎች የተሰሉ ግምቶች ናቸው፤ የፋይናንስ ምክር አይደሉም።"},
    "afford_fits": {"en": "fits: about {monthly} a month during construction", "am": "ይስማማል፤ በግንባታ ወቅት በወር ወደ {monthly}"},
    "afford_stretch": {"en": "a stretch: about {monthly} a month during construction", "am": "ከበጀት በላይ ነው፤ በግንባታ ወቅት በወር ወደ {monthly}"},
    "afford_loan": {
        "en": "After handover, financing {share}% with a bank loan over {years} years at about {rate}% would be roughly {loan} a month.",
        "am": "ከርክክብ በኋላ {share}%ን በባንክ ብድር ለ{years} ዓመት በ{rate}% ወለድ ቢያስይዙ በወር ወደ {loan} ይሆናል።"},
    "afford_cheapest": {"en": "The most affordable choice here is {title}.", "am": "ከዚህ ውስጥ በጣም አቅም የሚመጥነው {title} ነው።"},
    "found_intro": {"en": "Here is what I found:", "am": "ያገኘሁት ይህ ነው፦"},
    "ask_next": {"en": "Would you like to compare them or book a visit?", "am": "ሊያወዳድሯቸው ወይም ጉብኝት ሊይዙ ይፈልጋሉ?"},
    "widened": {"en": "Nothing matched exactly, so I widened the search.", "am": "ትክክለኛ ተዛማጅ ስላልተገኘ ፍለጋውን አስፋፍቻለሁ።"},
    "d_size": {"en": "{beds} bedrooms, {sqm} sqm, {unit}.", "am": "{beds} መኝታ ክፍል፣ {sqm} ካሬ ሜትር፣ {unit}።"},
    "d_construction": {"en": "Construction: {pct}% complete.", "am": "ግንባታ፦ {pct}% ተጠናቋል።"},
    "d_yield": {"en": "Estimated rental yield: {pct}%.", "am": "የሚጠበቅ የኪራይ ገቢ፦ {pct}%።"},
    "d_features": {"en": "Features: {items}.", "am": "ገጽታዎች፦ {items}።"},
    "d_plan": {"en": "Payment plan: {plan} (about {monthly} a month on average).", "am": "የክፍያ ዕቅድ፦ {plan} (በአማካይ በወር ወደ {monthly})።"},
    "d_3d": {"en": "I have sent you a 3D view of the layout and the 3D model file.", "am": "የክፍሎቹን 3D እይታና የ3D ሞዴል ፋይል ልኬልዎታለሁ።"},
    "btn_details": {"en": "Details {n}", "am": "ዝርዝር {n}"},
    "btn_book": {"en": "Book {n}", "am": "ቀጠሮ {n}"},
    "btn_compare": {"en": "Compare all", "am": "ሁሉንም አወዳድር"},
    "btn_consult": {"en": "Phone consultation", "am": "የስልክ ምክክር"},
    "proj_header": {"en": "These projects are in your area, but the developer has not published prices. I can ask for the price:", "am": "እነዚህ ፕሮጀክቶች በፈለጉት አካባቢ ናቸው፣ ግን አልሚው ዋጋ አላወጣም። ዋጋውን ልጠይቅልዎ እችላለሁ፦"},
    "btn_ask_price": {"en": "Ask the price", "am": "ዋጋውን ጠይቅ"},
    "proj_ask_hint": {"en": "The price is not published. Tap Ask the price and I will get it for you.", "am": "ዋጋው አልወጣም። «ዋጋውን ጠይቅ» ን ይጫኑ፣ አግኝቼ እነግርዎታለሁ።"},
    "proj_price_asked": {"en": "I have asked {developer} for the price of {project} and will message you here as soon as I have it. I can also arrange a site visit if you like.",
                         "am": "የ{project} ዋጋ ከ{developer} ጠይቄያለሁ፣ እንዳገኘሁ እዚሁ እነግርዎታለሁ። ከፈለጉ የቦታ ጉብኝትም ማመቻቸት እችላለሁ።"},
    "proj_price_again": {"en": "You already asked about {project}. I am waiting for {developer}'s answer and will tell you as soon as it arrives.",
                         "am": "ስለ {project} አስቀድመው ጠይቀዋል። የ{developer}ን መልስ እየጠበቅሁ ነው፣ እንደደረሰ እነግርዎታለሁ።"},
    "proj_price_known": {"en": "Good news: {project} by {developer} starts from {price}{note}. Would you like to book a site visit?",
                         "am": "ደስ የሚል ዜና፦ የ{developer} {project} ዋጋ ከ{price} ይጀምራል{note}። የቦታ ጉብኝት ልያዝልዎ?"},
    "proj_not_found": {"en": "I could not find that project. Tell me the area and size you want and I will look.", "am": "ያንን ፕሮጀክት ማግኘት አልቻልኩም። የሚፈልጉትን አካባቢና መጠን ይንገሩኝ፣ እፈልጋለሁ።"},
    "proj_also_header": {"en": "Also worth a visit (these developers have not published prices, but I can ask for them):", "am": "ለጉብኝት የሚያስቆጭ ሌላም አለ (እነዚህ አልሚዎች ዋጋ አላወጡም፣ ግን ልጠይቅ እችላለሁ)፦"},
    "btn_book_visit": {"en": "Book a visit", "am": "ጉብኝት ያዝ"},
    "proj_need_contact": {"en": "To book a visit to {project} I need your full name and phone number. Send them in one message, then tap Book a visit again.",
                          "am": "ወደ {project} ጉብኝት ለመያዝ ሙሉ ስምዎንና ስልክ ቁጥርዎን እፈልጋለሁ። በአንድ መልዕክት ይላኩልኝ፣ ከዚያ «ጉብኝት ያዝ» ን እንደገና ይጫኑ።"},
    "proj_slots": {"en": "{lead} I can offer these times for {project}:\n{options}\nWhich suits you?", "am": "{lead} ለ{project} እነዚህን ሰዓቶች ማቅረብ እችላለሁ፦\n{options}\nየትኛው ይስማማዎታል?"},
    "proj_no_slots": {"en": "I don't have an opening in the next week. Please suggest another date.", "am": "በሚቀጥለው ሳምንት ክፍት ሰዓት የለኝም። እባክዎ ሌላ ቀን ይጠቁሙ።"},
    "proj_slot_taken": {"en": "That time isn't free any more.", "am": "ያ ሰዓት አሁን ነጻ አይደለም።"},
    "proj_sold_out": {"en": "{project} is sold out. Tell me your area and size and I will find other options.", "am": "{project} ተሽጦ አልቋል። አካባቢና መጠን ይንገሩኝ፣ ሌላ አማራጭ እፈልጋለሁ።"},
    "proj_booked": {"en": "Your visit to {project} is booked for {when}. The price is confirmed with the developer, and {broker} will share the latest price and any offer at the visit. I have informed {developer}.",
                    "am": "የ{project} ጉብኝትዎ ለ{when} ተይዟል። ዋጋው ከአልሚው ጋር ይረጋገጣል፣ {broker} በጉብኝቱ ወቅት ወቅታዊውን ዋጋና ቅናሽ ያሳውቅዎታል። ለ{developer} አሳውቄያለሁ።"},
    "proj_price_dropped": {"en": "Price update: {project} by {developer} now starts from {price}{note}, down from {old} ({pct} lower). Would you like to book a site visit?",
                           "am": "የዋጋ ማሻሻያ፦ የ{developer} {project} አሁን ከ{price}{note} ይጀምራል፣ ከ{old} ({pct} ቀንሷል)። የቦታ ጉብኝት ልያዝልዎ?"},
    "btn_afford": {"en": "What can I afford?", "am": "ምን መግዛት እችላለሁ?"},
}


def msg(key: str, lang: str = "en", **values: object) -> str:
    entry = MESSAGES[key]
    return entry.get(lang, entry["en"]).format(**values)
