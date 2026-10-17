# Aldi Boodschappen voor Home Assistant

Boodschappenlijst met producten van **Aldi** en/of **Hoogvliet**, met prijzen. Kies in de instellingen in welke winkels je zoekt; kies je beide, dan zie je welk product het goedkoopst is.

- Typ een zoekterm (bijv. *pink lady*) en kies uit alle gevonden producten, met foto, hoeveelheid en prijs.
- **Prijzen vergelijken**: zoeken gaat over alle gekozen winkels tegelijk. Elk resultaat toont de prijs per kg, liter of stuk, je kunt sorteren op laagste prijs, en het goedkoopste product per eenheid krijgt een label.
- Markeer per product of het **elke week** terug moet komen.
- **Totaalprijs** van wat je nog moet halen, met een apart bedrag voor wat al in de wagen ligt en, als je bij meerdere winkels koopt, een subtotaal per winkel.
- **Lijst per winkel**: kies boven de lijst "Per winkel" om je boodschappen per winkel gegroepeerd te zien, met het bedrag per winkel. De keuze wordt onthouden in je browser.
- Eigen paneel **Boodschappen** in de zijbalk (live gesynchroniseerd tussen apparaten).
- Dezelfde lijst is beschikbaar als `todo.boodschappen`, dus ook bruikbaar in dashboards, automatiseringen en via Assist.
- **Een lijst per week, bewaard**: elke week heeft een eigen lijst die je later kunt terugzien. Boven de lijst kies je welke je bekijkt: de **huidige** (die van `todo.boodschappen`), een **komende** week waar je alvast aan werkt, of een eerdere week uit de **geschiedenis**. Met "Nieuwe lijst" maak je een lijst voor de volgende week, met alleen je terugkerende producten, alles uit de bronlijst, of leeg. Met "Uit andere lijst" kies je producten uit een andere week en zet je ze op de lijst die je bekijkt.
- **Nieuwe week**: de huidige lijst blijft bewaard. Heb je al een komende lijst klaargezet, dan wordt die de huidige; anders ontstaat er een nieuwe met je terugkerende producten (weer op "te halen"). Dit gebeurt automatisch op een dag/tijd naar keuze, of via de knop/service. Prijzen worden bijgewerkt voor de huidige en komende lijsten, niet voor de geschiedenis. Een bestaande lijst uit een eerdere versie wordt je eerste lijst.

> Dit is een onofficiële integratie en staat los van Aldi en Hoogvliet.

## Hoe het zoeken werkt: Aldi

De Aldi-webshop zoekt via een externe zoekdienst (Algolia). De integratie stuurt dezelfde zoekopdracht als de site, alleen naar de productenindex en met 48 in plaats van 1000 resultaten. Je krijgt dus ook varianten die niet in de naam staan: zoek je *pink lady*, dan vind je *Appels Pink Lady (6 stuks)*, met foto en prijs. Resultaten worden 10 minuten onthouden.

### De sleutel invullen (alleen voor Aldi)

Als je Aldi kiest, vraagt de integratie om een **Algolia API-sleutel**. Dat is de openbare, alleen-zoeken-sleutel die aldi.nl naar elke bezoeker stuurt; hij staat bewust niet in deze repository.

1. Open aldi.nl in Chrome en druk op **F12**.
2. Kies **Netwerk** → **Fetch/XHR** en zoek iets in de webshop.
3. Klik het verzoek aan dat naar `algolia.net` gaat. In de URL staat `x-algolia-api-key=…`; kopieer die waarde.
4. De **Application ID** (`2HU29PF6BH`) is al ingevuld.

De sleutel wordt bij het opslaan met een proefzoekopdracht gecontroleerd.

## Hoogvliet

hoogvliet.nl verbiedt in `robots.txt` voor bots het zoekpad (`/search`) en de Intershop-API waar de webshop zijn data vandaan haalt. Daar maakt deze integratie dan ook **geen** gebruik van. In plaats daarvan gebruikt hij de open prijsdata van [Checkjebon](https://github.com/supermarkt/checkjebon) (MIT-licentie; de data mag volgens het project hergebruikt worden):

- Het bestand wordt hooguit één keer per dag opgehaald, op de achtergrond, en alleen de Hoogvliet-producten worden lokaal bewaard. Zoeken doet dus geen verzoeken naar Hoogvliet; alleen het toevoegen van een product zonder afleidbaar productnummer doet er één (zie bij Foto's).
- Het eerste ophalen duurt even (het bestand bevat alle supermarkten). Zoek je daarvoor al, dan krijg je een melding dat de prijzen nog worden opgehaald, en de Aldi-resultaten staan er gewoon.
- Dit zijn de prijzen zoals Checkjebon ze het laatst heeft vastgelegd, **geen live prijzen** van de webshop. Check voor het boodschappen doen de prijs van belangrijke producten.
- **Foto's**: zie hieronder; met de huidige Checkjebon-data zijn er geen Hoogvliet-foto's.
- Het bestand is groot. Op een Raspberry Pi met weinig geheugen kan het verwerken veel werkgeheugen kosten; kies dan alleen Aldi.
- De structuur van het Checkjebon-bestand is niet door mij kunnen worden bekeken toen ik dit bouwde. De verwerking is defensief geschreven, en bij een onverwachte structuur staat in het log welke winkels er wel in stonden. Zet voor details `custom_components.aldi_boodschappen: debug` aan.

## Prijzen vergelijken

Pakketten verschillen van grootte, dus de losse prijs zegt weinig. Daarom rekent de integratie de hoeveelheid uit de omschrijving ("6 stuks", "500 g", "2 x 250 g", "75 cl") om naar een prijs per kg, per liter of per stuk. Het goedkoopste product per eenheid krijgt het label *Goedkoopst*, mits er minstens twee producten met dezelfde eenheid te vergelijken zijn. Producten waarvan de hoeveelheid niet te lezen is, krijgen geen eenheidsprijs en doen niet mee. Let op dat het vergelijkt wat je zoekt: bij *melk* kan het goedkoopste product per liter ook een ander soort melk zijn.

### Totaalprijs en prijzen bijwerken

Onder de lijst staat het totaal van de producten die je nog moet halen (prijs × aantal). Producten zonder prijs, zoals losse tekst, tellen niet mee en worden apart vermeld.

Prijzen veranderen, vooral bij aanbiedingen, en een terugkerend product blijft weken op je lijst staan. Daarom bewaart de integratie tot wanneer een prijs geldt. Is die datum voorbij, dan staat er een waarschuwing bij het product en onder het totaal. Met **Prijzen bijwerken** zoekt de integratie de producten op je lijst opnieuw op en haalt de actuele prijs binnen. Dat gebeurt ook automatisch bij een nieuwe week.

### Beperkingen

- **Onofficieel.** Aldi kan de sleutel vervangen of de index hernoemen; zoeken geeft dan een foutmelding. Haal dan een nieuwe sleutel op zoals hierboven en vul hem in onder *Configureren*. Is de indexnaam veranderd (nu `an_prd_nl_nl_products2`), dan moet `ALGOLIA_INDEX` in `const.py` aangepast worden.
- Bij Aldi is de prijs de actuele prijs uit de zoekdienst. Producten die niet beschikbaar zijn staan onderaan en zijn zo gemarkeerd.
- Staat iets er niet tussen, voeg het dan toe als losse tekst.
- Foutmeldingen staan in het log; zet voor meer detail `custom_components.aldi_boodschappen: debug` onder `logger:`.

## Installatie via HACS

1. Zet deze map in een eigen GitHub-repository en vervang `JOUW_GEBRUIKERSNAAM` in `manifest.json`.
2. HACS → ⋮ → **Aangepaste repositories** → voeg de repo-URL toe, categorie **Integratie**.
3. Download **Aldi Boodschappen** en herstart Home Assistant.
4. Instellingen → Apparaten & services → **Integratie toevoegen** → *Aldi Boodschappen*.

Had je eerder de Lidl-versie (`lidl_boodschappen`) geïnstalleerd? Verwijder die integratie en de map in HACS eerst; dit is een aparte integratie met een andere naam.

### Updates uitbrengen

```bash
git tag v0.2.1
git push origin v0.2.1
```

De workflow `release.yml` zet de versie in `manifest.json` gelijk aan de tag en maakt een GitHub-release. HACS laat dat daarna als update zien.

## Services

| Service | Doel |
| --- | --- |
| `aldi_boodschappen.new_week` | Nieuwe week starten (zie hierboven) |
| `aldi_boodschappen.add_item` | Product toevoegen (`name`, `quantity`, `recurring`) |

### Hoogvliet: geen eigen link of foto

De Checkjebon-data bevat voor Hoogvliet alleen naam, prijs, hoeveelheid en een link die Checkjebon zelf uit de naam maakt. Die link heeft geen productnummer en klopt dus niet met de echte Hoogvliet-pagina (`…/pink-lady-appels-op-schaal-726992000`). De integratie bewaart zo'n link daarom niet. Gevolgen:

- In de lijst opent een Hoogvliet-product de zoekpagina van Hoogvliet voor die naam.
- Er is geen foto (een winkelwagen-icoon), want het adres van de foto volgt uit het productnummer.
- Een dubbele merknaam in de data ("Pink lady Pink lady op schaal") wordt ingekort tot "Pink lady op schaal". Het ontbrekende woord ("Appels") staat niet in de data.

### Hoogvliet via de webshop van hoogvliet.nl (optioneel) — lees dit eerst

In de instellingen kies je bij **Bron voor Hoogvliet** tussen:

- **Checkjebon-prijslijst** (standaard): geen enkel verzoek naar hoogvliet.nl, maar ook geen foto's en geen echte productlinks.
- **Zoekfunctie van hoogvliet.nl**: foto's, echte links, volledige productnamen (merk + naam), de juiste hoeveelheid en of het product op voorraad is. De integratie vraagt bij elke zoekopdracht dezelfde GraphQL-API (`https://hoogvliet.nl/api/graphql`) op die de webshop zelf gebruikt.

> **Waarschuwing.** Dit is een niet-gedocumenteerde API van Hoogvliet, gebruikt zonder hun toestemming. Het pad `/api/graphql` staat niet in hun `robots.txt` (daarin staan `/search` en `/INTERSHOP/`), maar dat is geen toestemming: het blijft geautomatiseerd verkeer dat Hoogvliet kan blokkeren, en hun gebruiksvoorwaarden kunnen er iets over zeggen. De API kan ook zonder aankondiging veranderen. Gebruik het alleen voor persoonlijk gebruik, met een paar zoekopdrachten per keer. Door deze optie te kiezen neem je dat zelf voor je rekening.
>
> Om de belasting klein te houden doet de integratie: hooguit één verzoek per zoekopdracht (alleen de eerste 24 resultaten), nooit sneller dan één verzoek per 3 seconden, resultaten 10 minuten onthouden, niets op de achtergrond, en een eerlijke User-Agent (`HomeAssistant-AldiBoodschappen/…`, dus geen vermomming als browser). Er wordt niets omzeild.
>
> Mislukt een verzoek (of verandert Hoogvliet de API), dan valt de integratie terug op de Checkjebon-prijslijst en staat er een regel in het Home Assistant-log.
>
> Prijzen van producten die je via de webshop hebt toegevoegd worden niet automatisch bijgewerkt (alleen Aldi en Checkjebon-producten).
