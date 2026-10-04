"""
Builds HeatOS_Page_Guide.pdf: a plain-English walk through every part of the HeatOS build-plan page.
Run:  .venv/bin/python docs/make_guide.py
Screenshots are read from docs/shot_overview.webp and docs/shot_card.webp.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (BaseDocTemplate, Frame, Image as RLImage, KeepTogether, PageBreak,
                                PageTemplate, Paragraph, Spacer, Table, TableStyle)

HERE = Path(__file__).parent
OUT = HERE.parent / "HeatOS_Page_Guide.pdf"
TEAL, INK, MUTED, SOFT, LINE = colors.HexColor("#0f9d8a"), colors.HexColor("#14202b"), colors.HexColor("#5d6b79"), \
    colors.HexColor("#f1f6f7"), colors.HexColor("#dfe5ea")
P1, P2, P3, DC = colors.HexColor("#0f9d8a"), colors.HexColor("#d99a06"), colors.HexColor("#7c5cd6"), colors.HexColor("#e0405c")
T_PROT, T_FIRM, T_BASE, T_FLEX = [colors.HexColor(c) for c in ("#ff8fb1", "#5aa9ff", "#4ade80", "#fb923c")]
W = letter[0] - 1.5 * inch

# ------------------------------------------------------------------ styles
S = lambda name, **kw: ParagraphStyle(name, fontName=kw.pop("font", "Helvetica"), textColor=kw.pop("color", INK), **kw)
body = S("body", fontSize=10.5, leading=15, spaceAfter=6)
small = S("small", fontSize=9, leading=12.5, color=MUTED)
h1 = S("h1", font="Helvetica-Bold", fontSize=22, leading=26, spaceAfter=4)
h2 = S("h2", font="Helvetica-Bold", fontSize=15, leading=19, spaceBefore=10, spaceAfter=5, color=TEAL)
h3 = S("h3", font="Helvetica-Bold", fontSize=11.5, leading=15, spaceBefore=6, spaceAfter=2)
cell = S("cell", fontSize=9.5, leading=13)
cellb = S("cellb", font="Helvetica-Bold", fontSize=9.5, leading=13)
big = S("big", font="Helvetica-Bold", fontSize=26, leading=30)


def p(text, st=body):
    return Paragraph(text, st)


def callout(title, text, color=TEAL):
    t = Table([[Paragraph("<b>%s</b><br/>%s" % (title, text), cell)]], colWidths=[W])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SOFT), ("LINEBEFORE", (0, 0), (0, -1), 3.5, color),
                           ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                           ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    return KeepTogether([t, Spacer(1, 7)])


def swatches(items):
    """items: [(color, label, description)]"""
    rows = [[Table([[""]], colWidths=[13], rowHeights=[13], style=[("BACKGROUND", (0, 0), (-1, -1), c)]),
             Paragraph("<b>%s</b>" % lab, cell), Paragraph(desc, cell)] for c, lab, desc in items]
    t = Table(rows, colWidths=[22, 1.6 * inch, W - 22 - 1.6 * inch])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
                           ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    return t


def numbered(items):
    """Compact two-column key: (number, title, one line)"""
    cells = [Paragraph("<b>%s  %s</b><br/>%s" % t, cell) for t in items]
    cells += [""] * (len(cells) % 2)
    rows = [cells[i:i + 2] for i in range(0, len(cells), 2)]
    t = Table(rows, colWidths=[W / 2, W / 2])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
                           ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return t


def facts(rows, head=("Part", "What it means in plain words", "Watch out")):
    """Table with a header row: rows = [(name, meaning, caution)]"""
    data = [[Paragraph("<b>%s</b>" % h, cell) for h in head]] + \
           [[Paragraph("<b>%s</b>" % a, cell), Paragraph(b, cell), Paragraph(c, cell)] for a, b, c in rows]
    t = Table(data, colWidths=[1.25 * inch, 3.55 * inch, W - 4.8 * inch], repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), SOFT), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE), ("TOPPADDING", (0, 0), (-1, -1), 5),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    return t


# ------------------------------------------------------------------ annotated screenshots
CROP_TOP = 168          # removes the browser tab bar and address bar from the screenshots


def mark(src, dst, points, size=30, letters=False):
    im = Image.open(src).convert("RGB").crop((0, CROP_TOP, 2000, 1301))
    points = [(x, y - CROP_TOP) for x, y in points]
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", int(size * 1.15))
    for k, (x, y) in enumerate(points):
        d.ellipse([x - size, y - size, x + size, y + size], fill=(15, 157, 138), outline=(255, 255, 255), width=5)
        s = chr(65 + k) if letters else str(k + 1)
        w = d.textlength(s, font=f)
        d.text((x - w / 2, y - size * 0.62), s, fill=(255, 255, 255), font=f)
    im.save(dst)


mark(HERE / "shot_overview.webp", HERE / "_overview.png",
     [(432, 405), (432, 645), (432, 815), (432, 1010), (1530, 352), (1530, 700), (500, 1112), (900, 560), (1130, 790)])
mark(HERE / "shot_card.webp", HERE / "_card.png", [(1538, 455), (1538, 600), (1538, 720), (1538, 880), (1538, 1005)], letters=True)


def shot(path, caption, scale=1.0):
    im = Image.open(path)
    w = W * scale
    h = w * im.size[1] / im.size[0]
    img = RLImage(str(path), width=w, height=h)
    img.hAlign = "CENTER"
    return KeepTogether([img, Spacer(1, 3), p(caption, small), Spacer(1, 6)])


# ------------------------------------------------------------------ page furniture
def furniture(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.75 * inch, 0.5 * inch, "HeatOS page guide  |  Illustrative: all costs are placeholders until replaced with quotes")
    canvas.drawRightString(letter[0] - 0.75 * inch, 0.5 * inch, "Page %d" % doc.page)
    canvas.setStrokeColor(TEAL)
    canvas.setLineWidth(3)
    canvas.line(0.75 * inch, letter[1] - 0.55 * inch, 1.6 * inch, letter[1] - 0.55 * inch)
    canvas.restoreState()


doc = BaseDocTemplate(str(OUT), pagesize=letter, leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                      topMargin=0.8 * inch, bottomMargin=0.8 * inch, title="HeatOS page guide",
                      author="HeatOS team")
doc.addPageTemplates([PageTemplate(id="p", frames=[Frame(doc.leftMargin, doc.bottomMargin, W,
                                                         letter[1] - 1.6 * inch, id="f")], onPage=furniture)])
E = []

# ================================================================== PAGE 1: the big idea
E += [p("HeatOS build plan", h1),
      p("A plain-English guide to every part of the page", S("sub", fontSize=13, leading=17, color=MUTED, spaceAfter=10)),
      p("The big idea, in 30 seconds", h2),
      p("A big building at <b>111 8th Avenue</b> in Chelsea is full of computers. Computers make heat, like a laptop on your lap, "
        "and the building throws that warmth away. Meanwhile the neighbors burn gas, oil or steam to stay warm and to make hot showers."),
      p("<b>HeatOS</b> asks one question: <i>which neighbors should we send the computers' warmth to, in what order, and how?</i> "
        "We lay pipes under the streets. Warm water flows to the chosen buildings. A machine called a <b>heat pump</b> in each building squeezes "
        "that warmth until it is hot enough for radiators and showers. Each building keeps its old boiler as a backup."),
      p("This page is the control room for that plan. You can watch the network grow, see how much heat is used each month, and "
        "see what happens when things go wrong."),
      shot(HERE / "_overview.png", "The page, with every part numbered. The next pages explain each number.", scale=0.8),
      numbered([("1", "Big numbers", "Totals so far."), ("2", "Risk check", "Is the plan safe if things change?"),
                ("3", "Who pays for pipes?", "Three ways to pay, compared."), ("4", "Legend", "What the colors mean."),
                ("5", "Month chart", "Heat used in each month."), ("6", "Who gets heat first", "What happens if heat runs short."),
                ("7", "Sliders", "Move through time and the year."), ("8", "The data center", "The big red building."),
                ("9", "Pipes", "Streets with moving pulses of heat.")]),
      PageBreak()]

# ================================================================== PAGE 2: the map
E += [p("8 and 9: The map", h2),
      p("The map is a 3D picture of Chelsea. Every block is a real building drawn at its <b>real height</b> (from the city's building records). "
        "You can drag to turn it, scroll to zoom, and click a building to open its card."),
      swatches([(DC, "Red building", "The data center at 111 8th Avenue. All the warmth starts here."),
                (P1, "Teal = Phase 1", "The first buildings to join (2027): the ones whose heat pays back fastest."),
                (P2, "Yellow = Phase 2", "The next group (2030): buildings whose old boilers are close to worn out, so switching now saves them buying a new boiler."),
                (P3, "Purple = Phase 3", "The last group (2034): everyone else who still makes sense once the pipes are already nearby."),
                (colors.HexColor("#4a5260"), "Grey", "Not connected: too small, no energy data, or not worth the cost.")]),
      Spacer(1, 6),
      p("The pipes", h3),
      p("Colored lines on the streets are the pipes. The color shows the phase when that pipe is built. <b>Thicker pipe = more heat flowing through it</b> "
        "(the sum of the heat pumps downstream). The little glowing pulses show warm water moving <b>away from the data center</b>. "
        "In months when more heat is used, the pulses move faster and brighter."),
      p("We run the pipes along real streets, not through buildings, so the lengths are real street distances. "
        "All the buildings share one pipe tree: if three buildings are on the same street they share one pipe and share its cost."),
      callout("Why do some buildings turn on and off when I move the timeline slider?",
              "Because the plan is built in three steps. At \"Today\" nothing is built. At Phase 1 only the teal buildings and their pipes appear. "
              "At Phase 3 everything is built. Grey buildings never change."),
      p("The short lines into buildings", h3),
      p("From the street, a short pipe (a <b>lateral</b>) goes into each connected building. In the picture it ends at the middle of the building, so part of it is hidden inside the 3D block."),
      PageBreak()]

# ================================================================== PAGE 3: the big numbers
E += [p("1: The big numbers (top left)", h2),
      p("These six numbers add up everything built <b>so far on the timeline</b>. Slide the timeline from Today to Phase 3 and watch them grow. "
        "In the screenshot at Phase 2 (2030) they read 6 buildings; at Phase 3 (2034) they read 9."),
      facts([
          ("Buildings", "How many buildings are connected so far. Example: 9 at Phase 3.",
           "Only buildings big enough and with enough energy data are considered."),
          ("Pipe length", "All the pipe laid so far, in kilometers: street pipes plus the short pipes into buildings. Example: 4.94 km at Phase 3.",
           "Counted along real streets. The short pipes are a straight-line estimate x 1.3."),
          ("Capital cost", "The money that has to be spent up front by the project: heat pumps, the equipment that connects each building, and the short pipes. "
           "Example: $12.9M at Phase 3.",
           "In our base case Con Edison pays for the <b>street</b> pipes (see part 3), so they are NOT in this number. Every cost is a placeholder."),
          ("Heat used / yr", "The heat delivered into the buildings each year. Example: 73.1 GWh. (1 GWh = a million kWh, enough to heat roughly a hundred homes for a year.)",
           "The data center gives about 4/5 of this; the rest is the electricity the heat pumps use."),
          ("CO2 avoided / yr", "Tons of carbon dioxide we stop putting in the air each year, because less gas/oil/steam is burned. The electricity the heat pumps use is already subtracted. "
           "Example: 10,702 tons.", "Uses the city's LL97 pollution factors, which still need checking."),
          ("LL97 fines avoided / yr", "Local Law 97 fines buildings for too much pollution. This is the total yearly fine money the connected buildings no longer have to pay. Example: $2.1M.",
           "Only buildings that are over their limit today have fines to avoid."),
      ], head=("Number", "What it means in plain words", "Watch out")),
      Spacer(1, 6),
      callout("What is a kWh, MWh, GWh, kW?",
              "<b>kW</b> (kilowatt) is how big something is, like the size of an engine. <b>kWh</b> is how much it did, like how far the car went. "
              "1 MWh = 1,000 kWh. 1 GWh = 1,000,000 kWh. A kettle is about 2 kW. Running it for an hour uses 2 kWh."),
      PageBreak()]

# ================================================================== PAGE 4: risk + who pays
E += [p("2: Risk check", h2),
      p("Plans are only as good as their guesses, so we test what happens when the guesses are wrong."),
      facts([
          ("Yearly value across 60 price scenarios: $4M to $7.1M",
           "We re-ran the whole plan <b>60 times</b>, each time drawing different prices for gas, oil, steam and electricity, different pipe and heat pump costs, "
           "and a different amount of heat from the data center. We lined up the 60 results from worst to best. "
           "The <b>10th percentile</b> ($4M) means: in 9 out of 10 runs the plan earned at least this. The <b>90th percentile</b> ($7.1M) means: only 1 run in 10 did better. "
           "So the plan is worth roughly $4M to $7.1M a year.",
           "This is the value <b>before</b> paying for street pipes (in the base case Con Ed pays for them). The ranges we use are guesses."),
          ("If the biggest customer leaves: -65% value",
           "What if the largest building (the biggest heat user) says no, or leaves? We take it out and see what is left: about one third of the value. "
           "That tells you the plan <b>leans on one big customer</b>.",
           "A warning sign, not a failure: it says sign the big customer first, and look for backups."),
          ("If the data center makes 30% less heat: -20.5%",
           "What if some servers are turned off, a tenant leaves, or equipment breaks, and the data center gives 30% less warmth? "
           "Value drops about a fifth. It drops by less than 30% because the data center has some heat to spare most of the year.", ""),
      ]),
      p("3: Who pays for the street pipes?", h2),
      p("Street pipes are the biggest cost. It matters a lot <i>who</i> pays for them, because the more the buildings have to carry, "
        "the fewer buildings are worth connecting. We tried three options:"),
      facts([
          ("HeatOS pays all street pipes", "The project pays for every pipe itself. Only the best buildings can afford it: <b>4 buildings</b>, <b>$4.2M a year</b> left after paying for pipes, 9,133 tons CO2 avoided.", ""),
          ("50% grant", "Someone (for example the state) pays half of the street pipes. <b>7 buildings</b>, $4.1M a year after pipes, 9,925 tons of CO2.", ""),
          ("Utility rate-base (Con Ed)", "Con Edison builds and owns the pipes and earns the money back slowly through everyone's bills (this is how New York's thermal-network law is meant to work). "
           "<b>9 buildings</b>, $3.1M a year left after counting the pipes, 10,702 tons CO2. <b>This is our base case.</b>", ""),
      ], head=("Option", "What happens", "")),
      Spacer(1, 6),
      callout("Read it like this",
              "Paying for pipes yourself earns the most money but helps the fewest buildings. Letting Con Ed or a grant pay for the pipes connects "
              "more buildings and avoids about <b>1,600 more tons of CO2 a year</b> (about 17% more), at a cost to whoever pays. "
              "That price per ton is what the state or utility would be buying."),
      PageBreak()]

# ================================================================== PAGE 5: legend + month chart
E += [p("4: The legend", h2),
      p("Same colors as the map: red is the data center, teal/yellow/purple are the three phases, and grey is not connected. "
        "The phases are ordered like this:"),
      swatches([(P1, "Phase 1: fast payback", "Join first: the project earns its money back quickest."),
                (P2, "Phase 2: boilers near end of life", "Join next: the building's boiler is about to need replacing anyway. Public housing and buildings in a "
                                                              "state-listed disadvantaged community get a head start in the ranking."),
                (P3, "Phase 3: fill in the rest", "Join last, once pipes are already close.")]),
      p("5: Data center heat used, by month", h2),
      p("The twelve bars are the twelve months (J F M A M J J A S O N D). <b>A tall teal bar means we use almost all of the heat the data center makes that month. "
        "A short bar means most of the heat is wasted.</b> The grey part is heat nobody asked for."),
      p("The month you picked on the Month slider has a white outline. The sentence under the chart gives the numbers. "
        "In the screenshot: <b>July: 2,994 MWh of 7,472 MWh used (40%)</b>. That means the data center could have supplied 7,472 MWh of heat in July, and 2,994 MWh (40%) was used."),
      callout("Why are winter bars tall and summer bars short?",
              "In winter everybody wants heat for radiators and showers, so almost all the data center's heat is used. In summer nobody needs radiators, "
              "so only <b>hot water</b> (showers, kitchens, laundry) is left. The computers keep making warmth all year, so the summer heat goes unused. "
              "That is why we prefer buildings that need hot water all year, like hotels and hospitals."),
      p("The text under the chart says \"Heating season, the data center is almost fully used\" in winter and "
        "\"Mostly hot water now, so heat goes unused\" when use is below 40%."),
      PageBreak()]

# ================================================================== PAGE 6: who gets heat first
E += [p("6: Who gets heat first", h2),
      p("The data center makes only so much heat, like one pot of soup for a whole street. Most of the time there is enough. "
        "But what if there is <b>not</b> enough? Somebody has to be served first. HeatOS gives every customer a place on a ladder:"),
      swatches([(T_PROT, "Protected", "Public housing. Heat is <b>never cut</b>. Gets a 5% extra discount (equity)."),
                (T_FIRM, "Firm", "Hospitals and senior living. Heat is <b>never cut</b>. Pays 5% extra for that promise (a guarantee premium)."),
                (T_BASE, "Base", "Buildings that need hot water all year (hotels, many apartment buildings). Served after the two above."),
                (T_FLEX, "Flex", "Buildings that mostly need heating. Cheapest (5% extra discount) but <b>cut first</b> if heat is short. Their old boiler takes over for those hours.")]),
      Spacer(1, 4),
      p("What the three buttons mean", h3),
      facts([
          ("Normal year", "A typical year with typical weather and the data center working normally.", "Everyone gets all their heat."),
          ("Polar vortex", "A very cold winter: December to February, heating demand is <b>35% higher</b> than normal.",
           "In our plan nobody is cut. We size each heat pump to cover only a part (a half or a quarter) of the building's coldest-hour need, so the extra cold is handled by the building's own boiler and the heat pumps barely ask for more."),
          ("Data center -30%", "The data center makes <b>30% less heat</b> (servers turned off, a tenant leaves, equipment breaks).",
           "Here the ladder matters. Protected and Firm keep 100%, Base keeps about two thirds, and Flex keeps about a third at full build-out."),
      ], head=("Button", "What it means", "What you will see")),
      Spacer(1, 6),
      p("How to read the chart", h3),
      p("Along the bottom are the 24 hours of a typical day (0h = midnight, 12h = noon). The height is heat, in megawatts (MW), and the number in the top corner is the "
        "top of the scale. Each colored band is a rung of the ladder (pink, blue, green, orange from the bottom up). The <b>dashed white line</b> is how much heat the data center can give. "
        "<b>A red band</b> on top means heat was asked for but could not be given: those buildings use their boiler instead. "
        "If you see no red, nobody was cut."),
      p("The sentence below the chart says what share of the heat each rung got over a whole year. "
        "In a normal year every rung shows 100%. The chart changes with the Month slider, so you can compare a winter day to a summer day."),
      PageBreak()]

# ================================================================== PAGE 7: building card
E += [p("Clicking a building: its card", h2),
      p("Click any building on the map and a card opens on the right (and you can scroll the right column). It shows the plan for that one building. "
        "The example is <b>430 West 17th Street</b>, a public-housing building in Phase 1."),
      shot(HERE / "_card.png", "The building card. Letters A to E are explained below."),
      swatches([(TEAL, "A  Phase badge", "\"Phase 1 - 2027\": when this building joins. The card says if it is not built yet at the point you have chosen on the timeline."),
                (TEAL, "B  The money rows", "Heat pump size (7,255 kW is large); HeatOS price per unit of heat vs what the building pays today for fuel; "
                                              "yearly savings; LL97 fines avoided; payback."),
                (TEAL, "C  Tier, fit, data", "Service tier (protected here), heat fit score out of 100, whether the energy numbers are measured from real bills or estimated, "
                                               "and in how many of the 60 scenarios this building stayed in the plan."),
                (TEAL, "D  Fit bars", "Four short bars: temperature, timing, seasonality, capacity (see below). Longer is better."),
                (TEAL, "E  Open offer", "Opens a one-page offer for this building that you can save as a PDF.")]),
      PageBreak(),
      p("The card rows, one by one", h2),
      facts([
          ("Heat pump", "The size of the machine that raises the warm water's temperature in this building, in kilowatts (kW).", "We size it at half or a quarter of the building's coldest-hour need; the old boiler covers the rest."),
          ("HeatOS price", "What the building would pay per unit of heat. A MMBtu is a standard unit of heat; 1 MMBtu is about 293 kWh.", "Includes a share of the LL97 fines the building avoids, so it can look higher than today's fuel price."),
          ("Pays today (fuel)", "What the building pays now per unit of heat just for gas/oil/steam.", "It does not include fines."),
          ("Yearly savings", "What the building keeps each year after paying HeatOS: the fuel and fines it no longer pays, minus the HeatOS heat charge.", "Always positive by design: the building pays 90% (adjusted by tier) of what it avoids."),
          ("LL97 fines avoided", "Yearly fines the building no longer pays.", "Zero if the building was already under its limit."),
          ("Payback", "How many years until the project earns back the money it spent for this building (equipment, short pipe and its share of the shared pipes).", "It is the <b>project's</b> payback, not the building's. The building pays nothing up front."),
          ("Service tier", "Protected, firm, base or flex (see part 6).", ""),
          ("Heat fit", "A score out of 100 for how well this building matches the data center's steady heat. See the four bars below.", "A low score is not a no. The big public-housing building scores low on capacity but is worth a lot."),
          ("Energy data", "<b>Measured</b> means we used the building's real monthly energy reports. <b>Estimated</b> means we guessed from its floor area.", "Estimated buildings are less certain."),
          ("In plan in", "Out of the 60 price-and-cost scenarios, the share of times this building was chosen. 100% means a safe bet; 20% means it depends on prices.", ""),
      ], head=("Row", "What it means", "Watch out")),
      Spacer(1, 6),
      p("The four fit bars", h3),
      p("<b>Temperature:</b> how easy it is to raise the water's temperature (less lift means less electricity). "
        "<b>Timing:</b> how steady the building's use is through the day; a steady user matches the data center's steady heat. "
        "<b>Seasonality:</b> does it use heat all year (high) or only in winter (low)? Summer use is what stops heat being wasted. "
        "<b>Capacity:</b> how small the building's heat pump is next to the data center's heat; a very big building is a risk, so it scores low."),
      PageBreak()]

# ================================================================== PAGE 8: sliders + glossary + trust
E += [p("7: The sliders", h2),
      facts([
          ("Timeline", "Moves through time: <b>Today</b> (nothing built), then <b>Phase 1 (2027)</b>, <b>Phase 2 (2030)</b>, <b>Phase 3 (2034)</b>. "
                       "Pipes, buildings and all the numbers on the left grow as you move. The Play button steps through the phases by itself.", ""),
          ("Month", "Moves through the year. It changes the month chart, the waterfall chart and how fast the pulses move. "
                    "<b>Winter (Jan)</b> and <b>Summer (Jul)</b> jump straight to those months so you can compare.", ""),
      ], head=("Control", "What it does", "")),
      p("Words you will meet", h2),
      facts([
          ("Heat pump", "A machine like a fridge run backwards. It takes a lot of low warmth and squeezes it into hot water, using a little electricity. For every 1 unit of electricity it moves about 4 to 5 units of heat (that ratio is its COP).", ""),
          ("Boiler", "The building's old gas, oil or steam equipment. In HeatOS it stays as a backup and covers any heat the heat pump does not.", ""),
          ("LL97", "New York City's Local Law 97. It sets a yearly pollution limit for big buildings and fines them for going over.", ""),
          ("LL84", "The city's energy reporting law. The energy numbers in the plan come from these reports.", ""),
          ("Phase", "One of the three build steps (2027, 2030, 2034).", ""),
          ("Percentile", "Line up all results from worst to best. The 10th percentile is the result 10% of the way up (a cautious case); the 90th is 90% of the way (a lucky case).", ""),
          ("Con Ed / rate-base", "Con Edison, the utility. A rate-base project is paid for by the utility and repaid slowly through customers' bills.", ""),
          ("Disadvantaged community", "A neighborhood that New York State flags as facing extra pollution and money pressure. Buildings there get a head start in the ranking.", ""),
          ("MMBtu", "A unit of heat. 1 MMBtu is about 293 kWh.", ""),
      ], head=("Word", "Meaning", "")),
      PageBreak(),
      p("How much should you trust the page?", h2),
      swatches([(TEAL, "Real data", "Building shapes and heights, street distances, floor areas and ages, monthly heat use for most chosen buildings, weather, and how much electricity the data center building uses."),
                (P2, "Estimates", "How much heat the data center can give (about 10 MW, worked out from its electricity use), the temperature of that heat, and the share of fines each building avoids."),
                (DC, "Placeholders", "Pipe cost, heat pump cost, installation cost, fuel and electricity prices (old statewide averages scaled up), and the LL97 limits. These are the numbers to replace with quotes.")]),
      Spacer(1, 6),
      callout("The one thing to double-check",
              "The biggest customer (430 West 17th Street, public housing) carries most of the value. Its energy numbers need to be checked, because if they are wrong the plan changes a lot."),
      ]

doc.build(E)
print("wrote", OUT)
