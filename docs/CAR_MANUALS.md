# What iRacing's car manuals say, per car

Research for the Lap Analyzer's setup engineer (`CAR_KB` in `suite/web/analyzer/index.html`).
Source: the official manuals at https://www.iracing.com/resources/user-manuals/, read in October 2026.
Only what the manuals print is recorded. Where a manual is unclear it says so, and the analyzer
tells the driver to check the garage instead of guessing.

## The traps

- **A higher number is not always stiffer.** Corvette Z06 GT3.R ARB blades (6 softest), Aston Martin GT3 EVO and
  Toyota GR86 and Porsche Mission R *front* blades. Veloster N and Cross Car ARB arms: longer is softer.
- **0 is maximum damping** on the Corvette, Aston GT3 EVO, McLaren 720S, Audi R8 EVO II, Gen 3 Supercars and
  Cross Car. Negative scales (Audi R8 2019 and Porsche 911 GT3 R 2019: -24..0, BMW G82 GT4: -18..0) still have
  higher = more damping.
- **Traction control: higher = LESS help** on the Mercedes-AMG GT3 (2016 and 2020), Mercedes-AMG GT4, Ford GT
  GT3 and McLaren MP4-12C. ABS too on the Mercedes GT4; on the Mercedes GT3 2020 the manual is mixed.
- **Off positions differ:** 0, 1, 6, 10 or 12 depending on the car.
- **Not in number order:** Porsche 911 Cup ARB positions 1-2-3-4-7-5-6 (front, 992.2) and 1-2-3-4-5-7-6 (rear;
  both ends on the 992.1).
- **McLaren 720S front ARB:** the manual contradicts itself.
- **Diff ramp angles:** a lower angle locks more (GTP, LMP2, M8 GTE, SF23, IR18).

## Per car

| Car | Reversed | TC | ABS | Max downforce (wing, front, rear) | Wing +1 balanced by | Trim |
|---|---|---|---|---|---|---|
| [Acura ARX-06 GTP](https://s100.iracing.com/wp-content/uploads/2025/01/Acura-ARX-06-GTP-V2.pdf) |  | gain: higher = more aggressive cut, slip: higher = acts on smaller wheelspin |  | average front RH around 25 mm, rear around 45 mm |  |  |
| [Acura NSX GT3 EVO 22](https://s100.iracing.com/wp-content/uploads/2024/12/Acura-GT3_Manual_V2.pdf) |  | higher = more, off 1 | higher = more, off 1 | rear wing +11, dynamic front RH 40.0 mm (+/-2.5), dynamic rear RH 55.0 mm (+/-2.5) | Wing +1 is balanced by front RH -1 mm or rear RH +2 mm | GT3 list |
| [Aston Martin Vantage GT3 EVO](https://s100.iracing.com/wp-content/uploads/2026/01/Aston-Martin-GT3-Evo-V1.pdf) | arbblades@front, lowspeedcompressiondamping, highspeedcompressiondamping, lowspeedrebounddamping, highspeedrebounddamping | higher = more, off 0 | higher = more, off 0, rec. 3 | Rear Wing Angle +10.5, Dynamic Front RH 35.0 mm (+/-2.5), Dynamic Rear RH 70.0 mm (+/-2.5) | Wing +1 is balanced by Front RH -1.5 mm or Rear RH +4.0 mm (tips page says +4.5 mm) | under 250 km/h High; 250-270 Medium; over 270 Low to Minimum |
| [Aston Martin Vantage GT4](https://s100.iracing.com/wp-content/uploads/2025/03/Aston-Vantage-GT4-Manual_V4.pdf) |  | higher = more, off 0, rec. 1 | higher = more, off 0, rec. 7 |  |  | Daytona: Low, Le Mans: Low |
| [Audi R8 LMS GT3](https://s100.iracing.com/wp-content/uploads/2023/10/UM-Audi-R8-LMS-GT3-Manual.pdf) |  | higher = more, off 12, rec. 3-4 | higher = more, off 12, rec. 4 |  |  |  |
| [Audi R8 LMS EVO II GT3](https://s100.iracing.com/wp-content/uploads/2024/07/Audi-R8-LMS-EVO-II-GT3_Manual.pdf) | lowspeedcompressiondamping, highspeedcompressiondamping, lowspeedrebounddamping, highspeedrebounddamping | higher = more, off 12 | higher = more, off 12 | rear wing 7, dynamic front RH 40.0 mm (+/-2.5), rear RH 62.0 mm (+/-2.5) | Wing +1 is balanced by front RH -2.4 mm or rear RH +7.2 mm | GT3 list |
| [Audi RS3 LMS TCR](https://s100.iracing.com/wp-content/uploads/2025/03/Audi_RS3_LMS_TCR_V2.pdf) |  |  |  |  |  |  |
| [BMW M2 CSR](https://s100.iracing.com/wp-content/uploads/2024/12/BMW-M2-CSR-Manual_V2.pdf) |  |  |  |  |  |  |
| [BMW M4 GT4 (F82, 2018)](https://s100.iracing.com/wp-content/uploads/2024/07/BMW-M4-GT4-V2.pdf) |  |  |  |  |  | Daytona: Low, Le Mans: Low |
| [BMW M4 G82 GT4](https://s100.iracing.com/wp-content/uploads/2024/12/BMW-M4-G82-GT4-Manual-V2.pdf) |  | higher = more, off 1 |  |  |  | Daytona: Low, Le Mans: Low |
| [BMW M4 GT3](https://s100.iracing.com/wp-content/uploads/2023/10/BMW-M4-GT3-Manual.pdf) |  | higher = more, off 10, rec. 6 | higher = more, off 12, rec. 4 |  |  |  |
| [BMW M8 GTE](https://s100.iracing.com/wp-content/uploads/2024/09/BMW-M8-GTE_Manual_V2.pdf) | difframpangle | higher = more, off 10 |  |  |  |  |
| [BMW M Hybrid V8 GTP](https://s100.iracing.com/wp-content/uploads/2025/01/BMW-M-Hybrid-V8-V2.pdf) | difframpangle | gain: higher = more aggressive cut, slip: higher = acts on less wheelspin | none | average rear RH 50 mm, front as low as possible without bottoming |  | High by default; Spa: Medium; Le Mans: Low; Daytona: Low |
| [Cadillac V-Series.R GTP](https://s100.iracing.com/wp-content/uploads/2023/07/Cadillac-V-Series.R-GTP-Manual-V2.pdf) |  | gain: higher = more aggressive cut, slip: higher = more sensitive |  |  |  |  |
| [Chevrolet Corvette Z06 GT3.R](https://s100.iracing.com/wp-content/uploads/2024/06/Chevrolet-Corvette-Z06-GT3_manual_V2.pdf) | arbblades, lowspeedcompressiondamping, highspeedcompressiondamping, lowspeedrebounddamping, highspeedrebounddamping | higher = more, off 0 | higher = more, off 0 | Rear Wing Angle +9.5, Dynamic Front RH 37.5 mm (+/-2.5), Dynamic Rear RH 57.5 mm (+/-2.5) | Wing +1 is balanced by Front RH -1.5 mm or Rear RH +4.0 mm | GT3 list |
| [Dallara IR18](https://s100.iracing.com/wp-content/uploads/2026/01/Dallara-IR18-V3.pdf) | difframpangle | none | none |  |  |  |
| [Dallara P217 LMP2](https://s100.iracing.com/wp-content/uploads/2025/03/UM-Dallara-P217-LMP2-V2.pdf) | difframpangle |  |  | wing +17, 2 dive planes, both gurneys; dynamic front RH 5.0 mm, rear 50.0 mm | From 15 deg wing, 15.0/25.0 mm: wing +1 needs front -3.0 mm or rear +3.5 mm |  |
| [Ferrari 296 GT3](https://s100.iracing.com/wp-content/uploads/2024/06/Ferrari-296-GT3-V4.pdf) |  | higher = more, off 1 | higher = more, off 1 | Rear Wing Angle +10, Dynamic Front RH 37.5 mm (+/-2.5 mm), Dynamic Rear RH 58.5 mm (+/-2.5 mm) | Rear Wing Angle +1 is balanced by Front RH -1.0 mm or Rear RH +3.0 mm | GT3 list |
| [Ferrari 296 Challenge](https://s100.iracing.com/wp-content/uploads/2025/06/Ferrari-296-Challenge.pdf) |  | higher = more, off 0, rec. TC1 1-2 | higher = more, rec. 1-2 dry, 3-4 wet |  | Wing +1 needs rear RH +4 mm for the same aero balance |  |
| [Ferrari 488 GT3](https://s100.iracing.com/wp-content/uploads/2023/10/UM-Ferrari-488-GT3-Manual-V2.pdf) |  | higher = more, off 6, rec. 3 | higher = more, off 12, rec. 2 |  |  | GT3 list |
| [Ferrari 488 GT3 EVO 2020](https://s100.iracing.com/wp-content/uploads/2023/09/Ferrari-488-GT3-EVO-2020-Manual-V2.pdf) |  | higher = more, off 1, rec. 4 |  |  |  |  |
| [FIA Cross Car](https://s100.iracing.com/wp-content/uploads/2026/01/FIA-Cross-Car-V1.pdf) | bumpstiffness, reboundstiffness, armlength | none | none |  |  |  |
| [FIA F4](https://s100.iracing.com/wp-content/uploads/2023/10/FIA-F4-Manual-V2.pdf) |  | none | none |  | Rear wing is weaker than the front: needs bigger steps |  |
| [Ford GT GT3](https://s100.iracing.com/wp-content/uploads/2024/01/UM-Ford-GT-GT3-V2.pdf) |  | higher = less, off separate switch, rec. 2 | higher = more, off 12 |  |  |  |
| [Honda Civic Type R TCR](https://s100.iracing.com/wp-content/uploads/2025/03/Honda-Civic-Type-R_TCR.pdf) |  | none | higher = more, off 0 |  |  |  |
| [Hyundai Elantra N TCR](https://s100.iracing.com/wp-content/uploads/2025/03/Hyundai-Elantra-N-TCR_V3.pdf) |  |  | rec. 1 dry |  |  |  |
| [Hyundai Veloster N TC](https://s100.iracing.com/wp-content/uploads/2025/03/Hyundai-Veloster-N-TC-Manual-V4.pdf) | armlength | none |  |  |  |  |
| [Lamborghini Huracán GT3 EVO](https://s100.iracing.com/wp-content/uploads/2024/01/UM-Lamborghini-Huracan-GT3-Evo-V2.pdf) |  | higher = more, off 12, rec. 3-4 | higher = more, off 12 |  | Increasing rear wing needs more rake to keep the same aero balance |  |
| [Ligier JS P320](https://s100.iracing.com/wp-content/uploads/2025/03/Ligier-JS-P320-V2.pdf) |  | higher = more, off 0 |  |  |  |  |
| [McLaren 570S GT4](https://s100.iracing.com/wp-content/uploads/2024/07/McLaren-570S-GT4-Manual-V2.pdf) |  | rec. Track |  |  |  | Daytona: Low, Le Mans: Low |
| [McLaren 720S GT3 EVO](https://s100.iracing.com/wp-content/uploads/2024/09/Mclaren-720S-GT3_V2.pdf) | lowspeedcompressiondamping, highspeedcompressiondamping, lowspeedrebounddamping, highspeedrebounddamping | higher = more, off 1 | higher = more, off 1 | rear wing +10.5, dynamic front RH 35.0 mm (+/-2.5), dynamic rear RH 70.0 mm (+/-2.5) | Wing +1 is balanced by front RH -1.5 mm or rear RH +4.5 mm | GT3 list |
| [McLaren MP4-12C GT3](https://s100.iracing.com/wp-content/uploads/2024/01/UM-McLaren-MP4-12C-GT3-V3.pdf) | tractioncontrol | higher = less, rec. 2 | higher = more, off 12 |  |  | GT3 list |
| [Mercedes-AMG GT3 (2016)](https://s100.iracing.com/wp-content/uploads/2023/10/UM-Mercedes-AMG-GT3-Manual-V2.pdf) |  | higher = less, off separate switch, rec. 6 | higher = more, off 12, rec. 4 |  |  |  |
| [Mercedes-AMG GT3 2020](https://s100.iracing.com/wp-content/uploads/2024/07/Mercedes-AMG-GT3-2020-Manual_V9.pdf) |  | higher = less, off 12, rec. 8 | higher = less, off 12 | wing +9, dynamic front RH 40.0 mm (+/-2.5), dynamic rear RH 67.5 mm (+/-2.5) | Wing +1 is balanced by front RH -1.2 mm or rear RH +3.6 mm | GT3 list |
| [Mercedes-AMG GT4](https://s100.iracing.com/wp-content/uploads/2024/07/Mercedes-AMG-GT4-Manual-V2.pdf) |  | higher = less, off 12, rec. 10 | higher = less, off 12, rec. 4 |  |  | Daytona: Low, Le Mans: Low |
| [Mercedes-AMG W12 E Performance](https://s100.iracing.com/wp-content/uploads/2023/10/UM-Mercedes-AMG-F1-W12-E-Performance-V2.pdf) | enginebraking | none | none |  |  |  |
| [Ford Mustang GT3](https://s100.iracing.com/wp-content/uploads/2024/06/Mustang-GT3_Manual_V2.pdf) |  | higher = more, off 1 | higher = more, off 1 | rear wing +9, dynamic front RH 37.5 mm (+/-2.5), dynamic rear RH 57.5 mm (+/-2.5) | Wing +1 is balanced by front RH -1.5 mm or rear RH +4.0 mm | GT3 list |
| [Global Mazda MX-5 Cup](https://s100.iracing.com/wp-content/uploads/2024/07/Global-Mazda-MX5_Manual.pdf) |  |  |  |  |  |  |
| [Porsche 718 Cayman GT4](https://s100.iracing.com/wp-content/uploads/2024/07/Porsche-718-Cayman-GT4-Manual-V5.pdf) |  | higher = more, off 0 | higher = more, off 0 |  |  | Daytona: Low, Le Mans: Low |
| [Porsche 911 GT3 R (2019)](https://s100.iracing.com/wp-content/uploads/2023/10/UM-Porsche-911-GT3-R.pdf) |  | higher = more, off 0, rec. 5 | higher = more, off 0, rec. 4 |  |  |  |
| [Porsche 963 GTP](https://s100.iracing.com/wp-content/uploads/2025/03/Porsche-963-GTP-V2.pdf) | difframpangle | gain: higher = more aggressive cut, slip: higher = acts on smaller wheelspin |  | average rear RH ~45 mm, front ~25 mm |  |  |
| [Porsche 911 GT3 R (992)](https://s100.iracing.com/wp-content/uploads/2024/09/Porsche-911-GT3-R-992_V2.pdf) |  | higher = more, off 0 |  |  |  |  |
| [Porsche 911 GT3 Cup (992.1)](https://s100.iracing.com/wp-content/uploads/2024/07/Porsche-911-GT3-Cup-992_Manual.pdf) | order: arbblades 7 is slightly softer than 6 |  |  |  |  |  |
| [Porsche 911 Cup (992.2)](https://s100.iracing.com/wp-content/uploads/2026/01/Porsche-Cup-992.2-V1.pdf) | order: arbsetting@front [1, 2, 3, 4, 7, 5, 6]; arbsetting@rear [1, 2, 3, 4, 5, 7, 6] | higher = more, off 0 | higher = more, off 0 |  |  |  |
| [Porsche Mission R](https://s100.iracing.com/wp-content/uploads/2023/09/Porsche-Mission-R-real-Manual-V5-1.pdf) | arbblades@front | higher = more, off 0 | higher = more, off 0 |  |  |  |
| [Super Formula SF23](https://s100.iracing.com/wp-content/uploads/2024/04/Super-Formula-SF23_V4.pdf) | difframpangle | none | none |  |  |  |
| [Gen 3 Supercars](https://s100.iracing.com/wp-content/uploads/2024/12/Gen-3-Supercars-Manual_V3.pdf) | bumpstiffness, reboundstiffness, compressiondamping, rebounddamping | none | none |  |  |  |
| [Toyota GR86](https://s100.iracing.com/wp-content/uploads/2024/09/Toyota_GR86_Manual_V2.pdf) | arbblades@front | higher = more, off 0 | higher = more, off 0 |  |  |  |

Not covered (no manual reachable, or oval/dirt cars the road-course balance model does not fit):
Formula Vee, Super Formula Lights, Dallara iR-01, all NASCAR/ARCA/SRX/Legends/Late Model/Street Stock/Truck/
Mini Stock cars and the dirt oval cars. The analyzer still works for them, using plain number order.
