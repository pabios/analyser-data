"""Génère des fichiers de mesure fictifs, calqués sur de vrais exports du laboratoire.

La structure (en-têtes, colonnes, formats de date, séparateurs) reproduit des fichiers
réels ; seules les valeurs sont simulées : cycles charge / repos / décharge de 10 min.

exemples/
* GL980_Mes-_260601-170139.CSV  Graphtec GL980 tel qu'écrit par l'appareil (dans Excel,
      tout tient dans une seule colonne) : séparateur « , » et décimale « , », donc les
      nombres sont entre guillemets ("+24,003"). Bloc
      Vendor/Model/…, « AMP settings » (CH3 à CH8 actives, nom + unité), « XY settings »,
      « Position/Vernier settings », puis « Data » : Number;Date;Time;us;CH3…;Alarm;AlarmOut.
      100 ms. Pics de saturation et bruit de repos sur les courants.
* nanodac_Rd_Z.txt              Eurotherm nanodac — tabulation, décimale « , », date
      jj/mm/aa, une ligne d'en-tête dont l'unité est dans le descriptif de la voie, dans
      la même colonne : « Channel 2       (ENAN2);Group 1;M402-M210;°C ». 1 min.

exemples/autres_formats/
* GL860_1s.CSV                  Graphtec GL860 — « , » et décimale « . », en-tête double
      (No.,Date&Time,ms,CH1… puis NO.,Time,ms,V,mA…,A1234567890), 1 s, 670 points.
      Pics de saturation (pleine échelle ±, 1 à 3 points), parasites intermédiaires et
      « +++++++ » sur les voies de puissance ; petits courants (CH4, CH11, CH13) propres.

exemples/enceinte/  (issue #16 : fichiers d'enceinte climatique, température ET humidité)
* nanodac_T_HR.txt              Essai de chaleur humide cyclique de 24 h (1 min) au format nanodac :
      2 sondes de température (°C) et 2 sondes d'humidité (%HR), descriptif de voie dans l'en-tête.
      Deux sondes de chaque grandeur : de quoi essayer « Moyenne… » et « FFT… ».
* nanodac_T_HR.xlsx             Le même fichier enregistré sous Excel (date/heure en vraies dates).
* enceinte_T_HR.xlsx            Export d'enceinte sous Excel : « Date » et « Heure » dans deux
      colonnes, consigne et mesure de température et d'humidité.

exemples/autres_formats/
* GL860_test.GBD                Fichier natif Graphtec .GBD écrit par ce script (3 voies : 20 V, 200 mV,
      thermocouple K ; 100 ms ; 10 min) et GL860_test_export.CSV, les mêmes mesures en CSV Graphtec :
      les deux doivent donner les mêmes courbes. Ce .GBD suit la spécification Graphtec telle que le
      lecteur la comprend ; seul un .GBD écrit par l'appareil valide vraiment le lecteur.

Usage : python exemples/generer_exemples.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent
OTHER = OUT / "autres_formats"
rng = np.random.default_rng(42)

GL980_START = pd.Timestamp("2026-06-01 17:01:39")
GL980_DURATION_S = 40 * 60  # 40 min (le vrai fichier fait 48 h, 1,7 million de points)


def cycles(t, period_s=600.0, level=1.0):
    """Créneaux : charge 4 min, repos 1 min, décharge 4 min, repos 1 min."""
    phase = (t % period_s) / period_s
    out = np.zeros_like(t, dtype=float)
    out[phase < 0.4] = level
    out[(phase >= 0.5) & (phase < 0.9)] = -level
    return out


def add_spikes(y, n, level, width_max=3):
    y = y.copy()
    for pos in rng.choice(len(y) - 10, size=n, replace=False):
        w = rng.integers(1, width_max + 1)
        y[pos:pos + w] = level * rng.uniform(0.99, 1.0, size=w)
    return y


def fr(values, decimals):
    """Nombre signé, virgule décimale : +1,234 / -0,012."""
    return ["{:+.{}f}".format(v, decimals).replace(".", ",") for v in values]


def gl980():
    t = np.arange(0, GL980_DURATION_S, 0.1)
    n = len(t)
    stamps = GL980_START + pd.to_timedelta(t, unit="s")
    end = stamps[-1]

    u_alim = 24.0 + rng.normal(0, 0.02, n)
    i_s1 = 5.0 * cycles(t) + rng.normal(0, 0.004, n)
    i_s1 = add_spikes(i_s1, 25, 9.99)  # saturation pleine échelle ±10 A
    i_s1 = add_spikes(i_s1, 10, -9.99)
    i_s2 = 3.0 * cycles(t + 120) + rng.normal(0, 0.004, n)
    i_s2 = add_spikes(i_s2, 15, 9.99)
    u_s12 = 12.0 + 0.8 * cycles(t) + rng.normal(0, 0.01, n)
    i_s3 = 2.0 * cycles(t + 300) + rng.normal(0, 0.004, n)
    i_s4 = 2.0 * cycles(t + 420) + rng.normal(0, 0.004, n)

    def d(ts):
        return ts.strftime("%d/%m/%Y")

    def h(ts):
        return ts.strftime("%H:%M:%S")

    lines = [
        "Vendor;GRAPHTEC Corporation",
        "Model;GL980",
        "Version;Ver1,35;Rev0001",
        "MaxChannel;8",
        "Logic/Pulse;Off",
        "TempUnit;C",
        "Sampling interval;100ms",
        "Total data points;{}".format(n),
        "Trigger;0",
        "Start time;{};{}".format(d(GL980_START), h(GL980_START)),
        "End time;{};{}".format(d(end), h(end)),
        "Trigger time;{};{}".format(d(GL980_START), h(GL980_START)),
        "AMP settings",
        "CH;Signal name;Amp;Input;Range;Filter;Span;;Unit;Color;;;Line Width",
        "CH3;U_alim;M;DC;50V;Off;25;-25;V;5;23;30;0",
        "CH4;I_s1;M;DC;50mV;Off;10;-10;A;21;0;21;0",
        "CH5;I_s2;M;DC;50mV;Off;10;-10;A;28;0;17;0",
        "CH6;U_s1&2;M;DC;50V;Off;25;-25;V;28;5;6;0",
        "CH7;I_s3;M;DC;50mV;Off;10;-10;A;30;23;1;0",
        "CH8;I_s4;M;DC;50mV;Off;10;-10;A;5;23;30;0",
        "XY settings",
        "XY;Trace;X-Axis;Y-Axis",
        "XY1;On;CH1;CH2",
        "XY2;Off;CH1;CH3",
        "XY3;Off;CH1;CH4",
        "XY4;Off;CH1;CH2",
        "Position/Vernier settings",
        "CH;Position;Vernier",
    ] + ["CH{};50%;100,00%".format(k) for k in range(1, 9)] + [
        "Data",
        "Number;Date;Time;us;CH3;CH4;CH5;CH6;CH7;CH8;Alarm;AlarmOut",
    ]
    columns = [fr(u_alim, 3), fr(i_s1, 4), fr(i_s2, 4), fr(u_s12, 3), fr(i_s3, 4), fr(i_s4, 4)]
    dates, hours = stamps.strftime("%d/%m/%Y"), stamps.strftime("%H:%M:%S")
    micro = stamps.microsecond
    for k in range(n):
        alarm = "LLLLLLLLLL" if abs(i_s1[k]) < 9.9 else "LHLLLLLLLL"
        lines.append(";".join(
            [str(k + 1), dates[k], hours[k], str(micro[k])] + [c[k] for c in columns] + [alarm, "LLLL"]
        ))
    # Séparateur « , » : les champs contenant une virgule (décimales) sont entre guillemets
    lines = [",".join('"{}"'.format(c) if "," in c else c for c in line.split(";")) for line in lines]
    (OUT / "GL980_Mes-_260601-170139.CSV").write_bytes(("\r\n".join(lines) + "\r\n").encode("cp1252"))


def nanodac():
    # L'enceinte suit les cycles avec ~3 min de retard (à recaler avec le curseur)
    t = np.arange(-4 * 60, GL980_DURATION_S + 4 * 60, 60.0)
    heat = np.abs(cycles(t - 180.0))
    temp = np.empty(len(t))
    temp[0] = 27.5
    for k in range(1, len(t)):
        target = 27.5 + 1.2 * heat[k]
        temp[k] = temp[k - 1] + (target - temp[k - 1]) * (1 - np.exp(-60.0 / 150.0))
    temp += rng.normal(0, 0.08, len(t))
    stamps = GL980_START.floor("min") + pd.to_timedelta(t, unit="s")
    lines = ["Date/Heure\tChannel 2       (ENAN2);Group 1;M402-M210;°C"]
    for ts, v in zip(stamps, temp):
        lines.append("{}\t{}".format(ts.strftime("%d/%m/%y %H:%M:%S"), "{:.2f}".format(v).replace(".", ",")))
    (OUT / "nanodac_Rd_Z.txt").write_bytes(("\r\n".join(lines) + "\r\n").encode("cp1252"))


def gl860():
    start = pd.Timestamp("2026-09-10 14:53:39")
    n = 670
    t = np.arange(n, dtype=float)
    stamps = start + pd.to_timedelta(t, unit="s")
    amp = [  # CH, nom, plage, span, unité
        ("CH1", "U_alim", "50V", "0.100000,-0.100000", "V"),
        ("CH2", "I_spcC1", "200mV", "500.000000,-500.000000", "mA"),
        ("CH3", "CH3", "200mV", "25.000000,-25.000000", "mA"),
        ("CH4", "I_spc3", "200mV", "50.000000,-50.000000", "mA"),
        ("CH5", "CH5", "200mV", "100.000000,-100.000000", "mA"),
        ("CH6", "I_LH5", "200mV", "25.000000,-25.000000", "mA"),
        ("CH7", "CH7", "200mV", "100.000000,-100.000000", "mA"),
        ("CH8", "I_LB5", "50V", "0.025000,-0.025000", "V"),
        ("CH11", "I_LH7", "200mV", "500.000000,-500.000000", "mA"),
        ("CH13", "I_LB7", "100mV", "666.700000,-666.700000", "mA"),
    ]
    lines = [
        "Vendor,GRAPHTEC Corporation",
        "Model,GL860",
        "Firmware,Ver1.07",
        'Software,"Ver1.11"',
        "MaxChannel,30CH",
        "GSStartCH,30CH",
        "WLStartCH,30CH",
        "RTStartCH,30CH",
        "WLUnit,",
        "RTUnit,",
        "RTMaxCh,",
        "Sampling,1s",
        "Total data points,{}".format(n),
        "Trigger,0",
        "Start time,{},{}".format(start.strftime("%d/%m/%Y"), start.strftime("%H:%M:%S")),
        "Stop time,{},{}".format(stamps[-1].strftime("%d/%m/%Y"), stamps[-1].strftime("%H:%M:%S")),
        "Trigger time,{},{}".format(start.strftime("%d/%m/%Y"), start.strftime("%H:%M:%S")),
        "AC Mode,",
        "Logic/Pulse,Off",
        "Amp setting",
        "CH,Signal name,AMP,Input,Range,Temp Range,Filter,Span,,Unit",
    ] + ["{},{},SL3,DC,{},,Off,{},{}".format(ch, name, rng_, span, unit) for ch, name, rng_, span, unit in amp] + [
        "Data",
        "No.,Date&Time,ms," + ",".join(a[0] for a in amp) + ",Alarm1,Alarm2,Alarm3",
        "NO.,Time,ms," + ",".join(a[4] for a in amp) + ",A1234567890,A1234567890,A1234567890",
    ]
    base = [28.1, 122.8, 48.87, 0.02, 125.54, 48.65, 123.98, 27.81, 3.0, 6.67]
    values = [b + 0.3 * abs(b) * cycles(t, 120.0) + rng.normal(0, 0.01 * abs(b) + 0.01, n) for b in base]
    texts = [["{:+.2f}".format(x) for x in v] for v in values]

    # Pics de saturation (parasites de commutation, CEM) sur les voies de puissance :
    # pleine échelle positive ou négative, 1 à 3 points ; quelques hauteurs intermédiaires ;
    # quelques « +++++++ » (hors échelle). Les petits courants (CH4, CH11, CH13) restent
    # propres : le nettoyage ne doit pas les abîmer.
    full_scale = {0: 50.0, 1: 500.0, 2: 200.0, 4: 500.0, 5: 200.0, 6: 500.0, 7: 50.0}
    for col, fs in full_scale.items():
        for pos in rng.choice(np.arange(5, n - 5), size=8, replace=False):
            width = int(rng.integers(1, 4))
            kind = rng.random()
            for j in range(pos, pos + width):
                if kind < 0.6:
                    texts[col][j] = "{:+.2f}".format(fs * rng.uniform(0.985, 1.0))  # saturation +
                elif kind < 0.8:
                    texts[col][j] = "{:+.2f}".format(-fs * rng.uniform(0.985, 1.0))  # saturation -
                else:
                    texts[col][j] = "{:+.2f}".format(fs * rng.uniform(0.4, 0.7))  # parasite intermédiaire
        texts[col][int(rng.integers(20, n - 20))] = "+++++++"  # hors échelle écrit par la centrale

    for k in range(n):
        lines.append(",".join(
            [str(k + 1), stamps[k].strftime("%Y/%m/%d %H:%M:%S"), "0"]
            + [column[k] for column in texts]
            + ["LLLLLLLLLL"] * 3
        ))
    OTHER.mkdir(exist_ok=True)
    (OTHER / "GL860_1s.CSV").write_bytes(("\r\n".join(lines) + "\r\n").encode("cp1252"))


CLIM = OUT / "enceinte"
CLIM_START = pd.Timestamp("2026-11-02 08:00:00")


def climatic_profile():
    """Essai de chaleur humide cyclique (24 h, 1 min) : 25 °C / 95 %HR -> 55 °C / 93 %HR -> 25 °C."""
    r = np.random.default_rng(16)  # générateur à part : les autres exemples ne changent pas
    t = np.arange(0, 24 * 3600, 60.0)
    h = t / 3600.0
    # rampe 3 h, palier 9 h à 55 °C, descente 3 h, palier 9 h à 25 °C
    setpoint_t = np.interp(h, [0, 3, 12, 15, 24], [25, 55, 55, 25, 25])
    setpoint_h = np.interp(h, [0, 3, 12, 15, 24], [95, 93, 93, 95, 95])
    lag = 4  # l'enceinte suit la consigne avec ~4 min de retard
    follow_t = np.r_[np.full(lag, setpoint_t[0]), setpoint_t[:-lag]]
    follow_h = np.r_[np.full(lag, setpoint_h[0]), setpoint_h[:-lag]]
    n = len(t)
    t1 = follow_t + 0.15 + r.normal(0, 0.05, n)
    t2 = follow_t - 0.20 + r.normal(0, 0.05, n)
    rh1 = np.clip(follow_h - 0.8 + r.normal(0, 0.4, n), 0, 100)
    rh2 = np.clip(follow_h + 0.5 + r.normal(0, 0.4, n), 0, 100)
    # creux d'humidité pendant les rampes de température (comme sur une vraie enceinte)
    ramp = np.abs(np.gradient(follow_t, h)) > 1
    rh1[ramp] -= 6
    rh2[ramp] -= 5
    stamps = CLIM_START + pd.to_timedelta(t, unit="s")
    return stamps, setpoint_t, setpoint_h, [t1, t2], [rh1, rh2]


def climatic():
    import openpyxl

    CLIM.mkdir(exist_ok=True)
    stamps, sp_t, sp_h, temps, hums = climatic_profile()
    heads = ["Channel 1       (ENAN1);Group 1;M401-T120;°C", "Channel 2       (ENAN2);Group 1;M402-T121;°C",
             "Channel 3       (ENAN3);Group 1;M403-H110;%HR", "Channel 4       (ENAN4);Group 1;M404-H111;%HR"]
    columns = [np.round(temps[0], 2), np.round(temps[1], 2), np.round(hums[0], 1), np.round(hums[1], 1)]
    lines = ["Date/Heure\t" + "\t".join(heads)]
    for k, ts in enumerate(stamps):
        lines.append(ts.strftime("%d/%m/%y %H:%M:%S") + "\t" + "\t".join(
            "{:.2f}".format(c[k]).replace(".", ",") for c in columns))
    (CLIM / "nanodac_T_HR.txt").write_bytes(("\r\n".join(lines) + "\r\n").encode("cp1252"))

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "nanodac_T_HR"
    sheet.append(["Date/Heure"] + heads)
    for k, ts in enumerate(stamps):
        sheet.append([ts.to_pydatetime()] + [float(c[k]) for c in columns])
        sheet.cell(row=k + 2, column=1).number_format = "dd/mm/yy hh:mm:ss"
    book.save(CLIM / "nanodac_T_HR.xlsx")

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Export enceinte"
    sheet.append(["Date", "Heure", "Consigne T [°C]", "Température [°C]", "Consigne HR [%HR]", "Humidité [%HR]"])
    for k, ts in enumerate(stamps):
        moment = ts.to_pydatetime()
        sheet.append([moment.replace(hour=0, minute=0, second=0), moment.time(), float(sp_t[k]),
                      float(round(temps[0][k], 2)), float(sp_h[k]), float(round(hums[0][k], 1))])
        sheet.cell(row=k + 2, column=1).number_format = "dd/mm/yyyy"
        sheet.cell(row=k + 2, column=2).number_format = "hh:mm:ss"
    book.save(CLIM / "enceinte_T_HR.xlsx")


def gbd():
    """GL860 .GBD (en-tête Shift-JIS de 8192 octets + enregistrements big-endian) et son export CSV."""
    r = np.random.default_rng(1617)
    n, header_size = 6000, 8192  # 10 min à 100 ms
    start = pd.Timestamp("2026-11-06 14:00:00")
    k = np.arange(n)
    t = k * 0.1
    u = np.round(12.0 + 2.0 * cycles(t, 120.0) + r.normal(0, 0.01, n), 3)  # V, gamme 20 V : brut = V × 1000
    i = np.round(40.0 * cycles(t + 30, 120.0) + r.normal(0, 0.2, n), 2)  # mV, gamme 200 mV : brut = mV × 100
    tc = np.round(23.0 + 8.0 * (1 - np.exp(-t / 200.0)) + r.normal(0, 0.05, n), 1)  # °C : brut = °C × 10
    order = ["CH1", "CH2", "CH3", "Alarm", "AlOut", "Pulse1"]
    lines = ["$Common", "ID         = 261D1118", "Volume     = 1, 1", "HeaderSiz  = {}".format(header_size),
             "Model      = GL860", "$$Data", "Counts     = {}".format(n), "Order      = " + ", ".join(order),
             "Sample     = 100ms", "TempUnit   = ﾟC", "$$Time",
             "Start      = {}".format(start.strftime("%Y-%m-%d, %H:%M:%S")),
             "Trigger    = {}".format(start.strftime("%Y-%m-%d, %H:%M:%S")),
             "$Amp", "CH1 = DC, 20V, Off", "CH2 = DC, 200mV, Off", "CH3 = TEMP, TC_K, Off", "$EndHeader"]
    header = "\r\n".join(lines).encode("shift_jis")
    header += b"\x00" * (header_size - len(header))
    dtype = np.dtype([("CH1", ">i2"), ("CH2", ">i2"), ("CH3", ">i2"), ("Alarm", ">u2"), ("AlOut", ">u2"),
                      ("Pulse1", ">u4")])
    records = np.zeros(n, dtype=dtype)
    records["CH1"] = np.round(u * 1000)
    records["CH2"] = np.round(i * 100)
    records["CH3"] = np.round(tc * 10)
    records["Pulse1"] = k
    OTHER.mkdir(exist_ok=True)
    (OTHER / "GL860_test.GBD").write_bytes(header + records.tobytes())

    stamps = start + pd.to_timedelta(t, unit="s")
    csv = ["Vendor,GRAPHTEC Corporation", "Model,GL860", "Sampling,100ms", "Total data points,{}".format(n),
           "Start time,{},{}".format(start.strftime("%d/%m/%Y"), start.strftime("%H:%M:%S")),
           "Data", "No.,Date&Time,ms,CH1,CH2,CH3,Alarm1", "NO.,Time,ms,V,mV,degC,A1234567890"]
    for j in range(n):
        csv.append("{},{},{},{:+.3f},{:+.2f},{:+.1f},LLLLLLLLLL".format(
            j + 1, stamps[j].strftime("%Y/%m/%d %H:%M:%S"), stamps[j].microsecond // 1000, u[j], i[j], tc[j]))
    (OTHER / "GL860_test_export.CSV").write_bytes(("\r\n".join(csv) + "\r\n").encode("cp1252"))


if __name__ == "__main__":
    for old in OUT.glob("essai_*"):
        old.unlink()
    gl980()
    nanodac()
    gl860()
    climatic()
    gbd()
    print("Fichiers d'exemple générés dans", OUT)
