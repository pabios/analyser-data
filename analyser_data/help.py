"""Aide intégrée (boutons « Aide » et « ? »), en français et en anglais."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import APP_NAME
from .i18n import _, get_language
from .theme import C

# Clé de section (utilisée par les boutons « ? ») -> (titre FR, texte FR, titre EN, texte EN)
SECTIONS = [
    ("Démarrer", "Démarrer", """\
1. « Ouvrir des fichiers… » : choisissez un ou plusieurs exports (Graphtec, nanodac…) en
   texte (CSV, TXT, DAT, TSV), en classeur Excel (XLSX, XLS), en JSON, en Parquet, ou le
   fichier natif Graphtec .GBD (lecture à vérifier une fois avec l'export CSV).
   Séparateurs reconnus : « ; », « , », tabulation, « | » et espaces. La fenêtre « Import » montre
   comment chaque fichier est lu (colonne temps, voies, premières lignes) : choisissez la
   feuille d'un classeur, ou imposez le séparateur si la détection s'est trompée, puis
   « Importer ». Préférez le fichier d'origine de l'appareil (Excel coupe au-delà de
   1 048 576 lignes).
2. Cochez les voies à afficher. Cliquer sur un nom de fichier coche / décoche toutes
   ses voies. Chaque voie s'affiche sous le nom de la centrale : « Channel 4 ».
3. Nettoyez, corrigez, puis « Exporter » : seules les voies cochées sont exportées,
   avec les nettoyages et corrections.
4. Langue : liste « FR / EN » en haut à droite (choix mémorisé).""",
     "Getting started", """\
1. “Open files…”: choose one or more exports (Graphtec, nanodac…) as text (CSV, TXT,
   DAT, TSV), Excel workbooks (XLSX, XLS), JSON, Parquet, or the native Graphtec .GBD file
   (check it once against the CSV export). Separators recognised: “;”,
   “,”, tab, “|” and spaces. The “Import” window shows how each file is read
   (time column, channels, first rows): choose a workbook's sheet, or force the
   separator if detection went wrong, then “Import”. Prefer the device's original file
   (Excel truncates beyond 1,048,576 rows).
2. Tick the channels to display. Clicking a file name ticks / unticks all its
   channels. Each channel is shown with the data logger name: “Channel 4”.
3. Clean, correct, then “Export”: only ticked channels are exported, with their
   cleaning and corrections.
4. Language: “FR / EN” list at the top right (remembered)."""),

    ("Nettoyage", "Nettoyage", """\
Le nettoyage s'applique aux voies COCHÉES, quand vous cliquez sur « Nettoyer… ». La
fenêtre montre, voie par voie, ce qui sera modifié AVANT d'appliquer (« Aperçu »).
Tout est noté dans le journal et tout est réversible (« Annuler le nettoyage »).

Pics parasites (activé par défaut)
  Un groupe de points est un pic parasite s'il est étroit (5 points au plus, réglable ;
  à 100 ms, 5 points = 0,5 s) et s'il s'écarte nettement de la courbe des deux côtés
  (plus de 8 fois le bruit de mesure). Sa hauteur ne compte pas : un parasite de 1 A
  est retiré comme un de 10 A. Il est remplacé par le niveau de la courbe autour de lui.
  Les fronts de créneaux, paliers et décharges ne sont jamais touchés. Les trous courts
  (« +++++++ », « BURNOUT ») sont comblés par les points voisins.
  Option « Seulement la saturation » : ne retire que les pics au-delà de 98 % du max.

Réduire le bruit (activé par défaut : LISSAGE)
  Le bruit est lissé morceau par morceau entre les fronts : un créneau reste vertical,
  un palier garde sa valeur moyenne mesurée, une décharge garde sa forme. Le lissage
  seul ne met rien à 0. « Lissage sur … points » règle sa force (plus de points = plus
  lisse).

Forcer les repos à 0 (option, cumulable avec le lissage)
  Pendant les phases d'arrêt, les valeurs sous le seuil de la voie sont mises
  exactement à 0 (après le lissage). Le seuil est calculé sur le bruit réel de chaque
  voie, même si le repos est un peu décalé (ex. −0,04 A) : colonne « Suggestion »,
  modifiable dans la colonne « Seuil ». Une voie qui ne revient jamais à 0 (tension
  d'alimentation, petit courant permanent) n'est pas touchée. En rouge : plus de 90 %
  de la voie serait mise à 0 — c'est sans doute un vrai signal.

Décalage de zéro (option, désactivé par défaut)
  Si le repos d'une voie n'est pas exactement à 0 (ex. −0,04 A), ce niveau peut être
  soustrait à toute la voie. À n'utiliser que si vous êtes sûr qu'il s'agit d'une dérive
  du capteur et non d'un vrai courant.

Vérifier le résultat
  « Montrer les données brutes (en gris) » affiche, sous chaque voie nettoyée ou
  corrigée, ses données d'origine : on voit exactement ce qui a été modifié.""",
     "Cleaning", """\
Cleaning applies to the TICKED channels when you click “Clean…”. The window shows,
channel by channel, what will change BEFORE applying (“Preview”). Everything is
recorded in the log and can be undone (“Undo cleaning”).

Spurious spikes (on by default)
  A group of points is a spurious spike if it is narrow (5 points at most, adjustable;
  at 100 ms, 5 points = 0.5 s) and clearly departs from the curve on both sides (more
  than 8 times the measurement noise). Its height does not matter: a 1 A spike is
  removed like a 10 A one. It is replaced by the surrounding curve level. Square-wave
  edges, plateaus and discharges are never touched. Short gaps (“+++++++”,
  “BURNOUT”) are filled from neighbouring points.
  Option “Saturation only”: removes only spikes above 98 % of the maximum.

Reduce noise (on by default: SMOOTHING)
  Noise is smoothed piece by piece between edges: a square wave stays vertical, a
  plateau keeps its measured mean value, a discharge keeps its shape. Smoothing alone
  sets nothing to 0. “Smoothing over … points” sets its strength (more points =
  smoother).

Force rest phases to 0 (optional, can be combined with smoothing)
  During rest phases, values below the channel threshold are set exactly to 0 (after
  smoothing). The threshold is computed from each channel's real noise, even when the
  rest level is slightly offset (e.g. −0.04 A): “Suggestion” column, editable in the
  “Threshold” column. A channel that never returns to 0 (supply voltage, small
  permanent current) is left untouched. In red: more than 90 % of the channel would be
  set to 0 — it is probably a real signal.

Zero offset (optional, off by default)
  If a channel's rest level is not exactly 0 (e.g. −0.04 A), this level can be
  subtracted from the whole channel. Use it only if you are sure it is a sensor drift
  and not a real current.

Checking the result
  “Show raw data (in grey)” displays, under each cleaned or corrected channel, its
  original data: you see exactly what was changed."""),

    ("Correction au clic", "Correction au clic", """\
Clic gauche sur un point abîmé : il est remplacé par l'interpolation de ses deux
voisins. Désactivez d'abord la loupe / le déplacement de la barre d'outils (bouton
enfoncé = le clic sert à zoomer). Zoomez pour viser un point précis.""",
     "Click correction", """\
Left-click a damaged point: it is replaced by the interpolation of its two
neighbours. First disable the zoom / pan tool of the toolbar (pressed button = the
click zooms). Zoom in to target a precise point."""),

    ("Moyenne de voies", "Moyenne de voies", """\
Bouton « Moyenne… » de la carte « Voies de mesure » : cochez au moins deux voies de
même unité (ex. Channel 4 et Channel 5 en A, ou deux sondes °C) puis « Afficher la
moyenne ». Une voie « Moyenne Channel 4 / Channel 5 (A) » s'ajoute sous le fichier :
elle se coche, s'affiche, s'exporte et entre dans le rapport comme les autres.
C'est une voie calculée, point par point : les mesures d'origine ne changent pas. Elle
reprend les valeurs affichées de ses voies (nettoyage et corrections compris) et se
recalcule quand elles changent ; elle ne se nettoie pas et ne se corrige pas au clic
elle-même. Si une voie manque à un instant, il n'y a pas de moyenne à cet instant.
Voies de deux fichiers : celles du second sont interpolées sur les instants du premier,
jamais à travers une interruption d'enregistrement. « Retirer » (même fenêtre) la
supprime ; le journal garde la trace.""",
     "Channel average", """\
“Average…” button of the “Channels” card: tick at least two channels with the same unit
(e.g. Channel 4 and Channel 5 in A, or two °C probes) then “Show the average”. A channel
“Average Channel 4 / Channel 5 (A)” is added under the file: it can be ticked, shown,
exported and reported like the others.
It is a calculated channel, point by point: the original measurements do not change. It
uses the displayed values of its channels (cleaning and corrections included) and is
recalculated when they change; it is not cleaned or click-corrected itself. If a channel
is missing at an instant, there is no average at that instant.
Channels from two files: those of the second are interpolated onto the timestamps of the
first, never across a recording interruption. “Remove” (same window) deletes it; the log
keeps a record."""),

    ("Spectre FFT", "Spectre FFT", """\
Bouton « FFT… » de la carte « Voies de mesure » : spectre d'amplitude des voies cochées
(cochez-en d'autres dans la fenêtre). Calcul sur la zone affichée du graphique (zoom) ou
sur tout l'essai, avec les valeurs affichées (nettoyage et corrections compris).
Méthode : moyenne retirée (donnée à part dans le tableau), fenêtre de Hann ; une
sinusoïde d'amplitude A donne un pic de hauteur A, dans l'unité de la voie. Un graphique
par unité ; tableau : pic principal (fréquence et période), amplitude, moyenne, points
analysés. Les points manquants (trous, interruptions d'enregistrement) sont comblés par
interpolation pour le calcul et comptés : choisissez une zone sans interruption pour un
spectre exact. Échelles log possibles ; export du spectre en CSV et en image. Les
mesures ne sont pas modifiées.""",
     "FFT spectrum", """\
“FFT…” button of the “Channels” card: amplitude spectrum of the ticked channels (tick
others in the window). Calculated on the displayed area of the chart (zoom) or on the
whole test, with the displayed values (cleaning and corrections included).
Method: mean removed (given separately in the table), Hann window; a sine of amplitude A
gives a peak of height A, in the channel unit. One chart per unit; table: main peak
(frequency and period), amplitude, mean, analysed points. Missing points (holes,
recording interruptions) are filled by interpolation for the calculation and counted:
choose an area without interruption for an exact spectrum. Log scales available; export
of the spectrum as CSV and image. Measurements are not modified."""),

    ("Axes et courbes", "Axes et courbes", """\
Bouton « Modifier les axes et les courbes » de la barre d'outils (icône de courbe, à
gauche de la disquette) :
• Onglet Axes : titre du graphique ; début et fin du temps (H:MM:SS, ex. 1:30:00 ou
  48:00:00) ; pour chaque axe Y (signaux électriques, °C, %HR) : min, max, libellé et
  échelle linéaire ou logarithmique. Champ vide = automatique.
• « Graduations tous les » (chaque axe) : Automatique, ou un pas de la liste (axe du
  temps : 30 min, 1 h, 2 h, 6 h, 12 h, 1 jour ; autres axes : 0,5, 1, 5, 10…), ou un pas
  saisi (« 15 min », « 1:30:00 », « 2 j », « 2,5 »). Appliqué tout de suite et mémorisé
  pour les prochaines ouvertures. Si le pas est trop fin pour la période affichée, un
  multiple du pas est utilisé ; en zoomant, le pas choisi revient.
• Onglet Courbes : nom affiché dans la légende, couleur (cliquer sur la pastille),
  épaisseur, type de trait, marqueurs et « Valeur absolue |x| » de chaque voie. La
  valeur absolue ne change que l'affichage (graphique, image, rapport) : le CSV exporté
  garde les vraies valeurs signées ; elle est notée dans le journal.
Par défaut, la légende affiche « Channel 1 (V) » et la température « Température
ambiante » (numérotée s'il y en a plusieurs) ; les voies d'un fichier d'enceinte
climatique gardent leur vrai nom (« Channel 2 – M402-M210 (°C) ») ; le titre de l'axe Y regroupe les
grandeurs tracées (« Tension (V) / Courant (mA) »). Un titre effacé revient au défaut.
Les réglages restent quand vous cochez d'autres voies ou nettoyez, et sont repris dans
l'image et le rapport PDF. La légende est toujours hors des courbes : à droite par
défaut (plusieurs colonnes s'il y a beaucoup de voies), en haut ou en bas (liste
« Légende » de l'onglet Axes) ; le bouton « Légende » de la barre d'outils la masque
ou la réaffiche. Le bouton « Maison » remet les limites automatiques (titres,
libellés et couleurs gardés) ; « Tout remettre par défaut » efface tout.""",
     "Axes and curves", """\
“Edit axes and curves” button of the toolbar (curve icon, left of the floppy disk):
• Axes tab: chart title; start and end time (H:MM:SS, e.g. 1:30:00 or 48:00:00); for
  each Y axis (electrical signals, °C, %RH): min, max, label and linear or logarithmic
  scale. Empty field = automatic.
• “Ticks every” (each axis): Automatic, or a step from the list (time axis: 30 min, 1 h,
  2 h, 6 h, 12 h, 1 day; other axes: 0.5, 1, 5, 10…), or a typed step (“15 min”,
  “1:30:00”, “2 d”, “2.5”). Applied at once and remembered for the next sessions. If the
  step is too fine for the displayed period, a multiple of it is used; zooming in brings
  the chosen step back.
• Curves tab: name shown in the legend, colour (click the swatch), width, line style,
  markers and “Absolute value |x|” of each channel. The absolute value only changes the
  display (chart, image, report): the exported CSV keeps the real signed values; it is
  recorded in the log.
By default, the legend shows “Channel 1 (V)” and the temperature “Ambient temperature”
(numbered if there are several); channels of a climatic chamber file keep their real name
(“Channel 2 – M402-M210 (°C)”); the Y axis title groups the plotted quantities
(“Voltage (V) / Current (mA)”). A cleared title returns to the default.
Settings are kept when you tick other channels or clean, and are used in the image and
the PDF report. The legend is always outside the curves: on the right by default (several
columns when there are many channels), at the top or bottom (“Legend” list of the Axes
tab); the “Legend” toolbar button hides or shows it. The “Home” button restores automatic limits (titles, labels and colours
are kept); “Reset all to default” clears everything."""),

    ("Cycles", "Cycles — essai plus long", """\
Carte « Cycles » → « Cycles… » : rallonger ou raccourcir un essai en dupliquant ou en
retirant des cycles entiers.
• Voie de référence : la voie qui définit les cycles (le courant de cyclage en général).
  Les cycles sont détectés et numérotés (début, durée, min, max) ; les pics parasites
  ne créent pas de faux cycles.
• « Dupliquer le cycle N, k fois, insérés après le cycle M » (0 = au début) ou
  « Retirer les cycles N1 à N2 ».
• Toutes les voies du fichier sont coupées / complétées aux mêmes points, au début exact
  d'un cycle : la forme d'onde est conservée et le temps reste continu. Une dérive de
  niveau au raccord est compensée en douceur, sans toucher au front.
• Aperçu avant d'appliquer : signal avant / après (raccords en pointillés) et spectre
  FFT avant / après ; la fréquence fondamentale f0 doit rester la même (✓).
• Le signal et le temps restent continus ; le graphique ne marque pas les cycles ajoutés.
  Mais les cycles AJOUTÉS NE SONT PAS DES MESURES : ils sont notés dans le journal,
  marqués dans le CSV (colonne « Cycle ajouté (non mesuré) » = 1) et signalés dans le
  rapport PDF. « Annuler la modification » revient en arrière.""",
     "Cycles — longer test", """\
“Cycles” card → “Cycles…”: lengthen or shorten a test by duplicating or removing whole
cycles.
• Reference channel: the channel that defines the cycles (usually the cycling current).
  Cycles are detected and numbered (start, duration, min, max); spurious spikes do not
  create false cycles.
• “Duplicate cycle N, k times, inserted after cycle M” (0 = at the start) or “Remove
  cycles N1 to N2”.
• Every channel of the file is cut / extended at the same points, at the exact start of a
  cycle: the waveform is kept and time stays continuous. A level drift at the joint is
  smoothly compensated without touching the edge.
• Preview before applying: signal before / after (joints dotted) and FFT spectrum before
  / after; the fundamental frequency f0 must stay the same (✓).
• Signal and time stay continuous; the chart does not mark the added cycles. But ADDED
  cycles ARE NOT MEASUREMENTS: they are recorded in the log, flagged in the CSV (“Added
  cycle (not measured)” column = 1) and reported in the PDF report. “Undo the change”
  goes back."""),

    ("Base de temps", "Base de temps", """\
Pour superposer plusieurs fichiers :
• Heure réelle : chaque mesure à son heure (même essai, appareils à l'heure).
• Période commune : seulement la période où tous les fichiers mesurent ; ils
  commencent et finissent ensemble.
• Débuts à 0 : chaque fichier démarre à 0 (horloges pas à l'heure).
• Durée étirée : chaque fichier de 0 à 100 % de sa durée, pour comparer la forme de
  deux essais (le temps est déformé).
Grille d'export : celle du fichier de référence, ou une grille commune (100 ms, 1 s…)
pour avoir une valeur de chaque appareil sur chaque ligne (mesure la plus proche).

Décalage enceinte climatique : décale dans le temps les voies de température si
l'enceinte réagit avec retard. Par défaut, toutes les voies °C (quel que soit leur
fichier) suivent le curseur ; les autres restent à leur heure. « Voies décalées… » :
cochez vous-même les voies à décaler (« Toutes les °C », « Aucune »).

Corrections de température et d'humidité (carte « Enceinte climatique ») : valeur de
-5,0 à +5,0 (°C ou %HR), par pas de 0,1, ajoutée aux voies de la grandeur : valeur
affichée = valeur mesurée + correction. « Par sonde… » donne à une sonde sa propre
correction, prioritaire sur la valeur générale (champ vide : valeur générale). Les
corrections s'appliquent au graphique, à la légende (« Température ambiante (+0,5 °C) »),
au CSV, à l'image et au rapport PDF, et sont notées dans le journal. Les mesures d'origine
ne sont jamais modifiées : remettez 0 pour les retrouver. Les valeurs sont mémorisées
d'un lancement à l'autre (par sonde : d'après le nom du fichier et de la voie).""",
     "Time base", """\
To overlay several files:
• Real time: each measurement at its clock time (same test, devices on time).
• Common period: only the period where all files are recording; they start and
  end together.
• Starts at 0: each file starts at 0 (device clocks not set).
• Stretched duration: each file from 0 to 100 % of its duration, to compare the
  shape of two tests (time is distorted).
Export grid: the reference file's grid, or a common grid (100 ms, 1 s…) to get one
value from each device on every row (nearest measurement).

Climatic chamber shift: shifts the temperature channels in time if the chamber lags.
By default, every °C channel (whatever its file) follows the slider; the others keep
their time. “Shifted channels…”: tick the channels to shift yourself (“All °C”,
“None”).

Temperature and humidity corrections (“Climatic chamber” card): value from -5.0 to
+5.0 (°C or %RH), in 0.1 steps, added to the channels of that quantity: displayed value
= measured value + correction. “Per probe…” gives a probe its own correction, which
overrides the general value (empty field: general value). Corrections apply to the
chart, the legend (“Ambient temperature (+0.5 °C)”), the CSV, the image and the PDF
report, and are recorded in the log. The original measurements are never changed: set
0 to get them back. Values are remembered between launches (per probe: by file and
channel name)."""),

    ("Livrables pour le client", "Livrables pour le client", """\
Menu « Exporter » :
• Données nettoyées (CSV + journal) : le CSV « ; » des voies cochées, et à côté un fichier
  « …_journal.txt » qui liste tous les traitements appliqués (traçabilité).
• Image du graphique (PNG, PDF, SVG) : le graphique tel qu'affiché (même zoom), au
  format A4 paysage, pour l'insérer dans un rapport.
• Rapport PDF pour le client : synthèse (fichiers sources, appareils, périodes, base de
  temps), statistiques par voie sur la période affichée (min, max, moyenne,
  écart-type, points modifiés), graphique et journal des traitements.
Astuce : zoomez sur la période utile avant d'exporter l'image ou le rapport.""",
     "Customer deliverables", """\
“Export” menu:
• Cleaned data (CSV + log): the “;” CSV of the ticked channels, plus a
  “…_journal.txt” file listing every treatment applied (traceability).
• Chart image (PNG, PDF, SVG): the chart as displayed (same zoom), A4 landscape, to
  insert in a report.
• PDF report for the customer: summary (source files, devices, periods, time base),
  per-channel statistics over the period shown (min, max, mean, standard deviation,
  modified points), chart and processing log.
Tip: zoom on the useful period before exporting the image or the report."""),

    ("En cas de problème", "En cas de problème", """\
Les longues opérations (ouverture, nettoyage, exports) affichent leur avancement ; la
fenêtre reste utilisable. En cas d'erreur, un message l'explique et les détails
techniques sont enregistrés dans le fichier « analyser_data_erreurs.log » de votre
dossier personnel (C:\\Users\\<vous>) : envoyez-le au développeur avec une capture.

Fichier refusé : le message indique ce qui a été lu. Répondez « Oui » pour enregistrer
un extrait (début et fin du fichier, quelques Ko) et envoyez-le au développeur.
Vérifiez aussi la version dans le titre de la fenêtre.""",
     "Troubleshooting", """\
Long operations (opening, cleaning, exports) show their progress; the window stays
usable. If an error occurs, a message explains it and the technical details are saved
to the “analyser_data_erreurs.log” file in your home folder (C:\\Users\\<you>): send it to
the developer with a screenshot.

Rejected file: the message shows what was read. Answer “Yes” to save an excerpt
(beginning and end of the file, a few KB) and send it to the developer. Also check
the version in the window title."""),
]


class HelpWindow(tk.Toplevel):
    """Fenêtre d'aide ; ``show(section)`` fait défiler jusqu'à une section."""

    _instance = None

    @classmethod
    def open(cls, master, section: str = "Démarrer") -> "HelpWindow":
        if cls._instance is None or not cls._instance.winfo_exists():
            cls._instance = cls(master)
        cls._instance.show(section)
        return cls._instance

    def __init__(self, master):
        super().__init__(master)
        english = get_language() == "en"
        self.title(_("Aide") + " — " + APP_NAME)
        self.geometry("760x660")
        self.configure(background=C["background"])
        outer = tk.Frame(self, background=C["card"], highlightbackground=C["border"], highlightthickness=1)
        outer.pack(fill=tk.BOTH, expand=True, padx=16, pady=(16, 8))
        frame = ttk.Frame(outer, style="Card.TFrame", padding=4)
        frame.pack(fill=tk.BOTH, expand=True)
        self.text = tk.Text(frame, wrap=tk.WORD, padx=18, pady=12, font=("TkDefaultFont", 10), relief=tk.FLAT,
                            background=C["card"], foreground=C["fg_soft"], highlightthickness=0, bd=0,
                            spacing1=1, spacing3=1)
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.tag_configure("title", font=("TkDefaultFont", 13, "bold"), foreground=C["fg"],
                                spacing1=14, spacing3=6)
        for key, title_fr, body_fr, title_en, body_en in SECTIONS:
            self.text.mark_set("section-" + key, self.text.index("end-1c"))
            self.text.mark_gravity("section-" + key, tk.LEFT)
            self.text.insert(tk.END, (title_en if english else title_fr) + "\n", "title")
            self.text.insert(tk.END, (body_en if english else body_fr) + "\n\n")
        self.text.configure(state=tk.DISABLED)
        ttk.Button(self, text=_("Fermer"), style="Primary.TButton", command=self.destroy).pack(
            anchor=tk.E, padx=16, pady=(0, 16))

    def show(self, section: str) -> None:
        self.deiconify()
        self.lift()
        mark = "section-" + section
        if mark in self.text.mark_names():
            self.update_idletasks()
            self.text.yview(mark)
