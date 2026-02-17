# Use Cases

Once the AquaClean is connected and publishing to MQTT, the `is_user_sitting` event can be used as a trigger for all kinds of home automation scenarios.

---

## Greet the user as they take their seat

When `is_user_sitting` changes to `true`, have your home automation system play a greeting via a smart speaker:

> *„Schön, dass Du Platz genommen hast."*

---

## Active Noise Cancellation — play music during a session

Start music playback automatically when the user sits down, to minimise ambient noise during the session.

Example track well-suited for the occasion:

> *Die schöne Müllerin, Op. 25, D. 795: Wohin? — Ich hört ein Bächlein rauschen*
> Fritz Wunderlich · Franz Schubert

Stop playback when `is_user_sitting` changes back to `false`.

---

## Dismiss the user when they leave

When `is_user_sitting` returns to `false`, play a closing remark:

> *„Prima, dass wir ins Geschäft gekommen waren."*

---

## Inform the user of the session duration

Record the timestamp when `is_user_sitting` becomes `true` and again when it returns to `false`. Calculate the elapsed time and announce it:

> *„Wir hatten für 3 Minuten und 19 Sekunden das Vergnügen."*

---

## Control additional lights

Use `is_user_sitting` as a proximity sensor to switch on supplementary bathroom lights (e.g. floor lighting or mirror backlighting) beyond the AquaClean's built-in orientation light.

> **Note:** `OrientationLightState` always reads `0` regardless of the actual light state — this appears to be a firmware limitation, confirmed in the original C# library as well. Use `is_user_sitting` as the trigger instead.

---

## Voice control

With Home Assistant or openHAB connected via MQTT, all of the above can be triggered or overridden by voice (Amazon Alexa, Google Assistant, Apple Siri) — e.g. *"Hey Siri, open the lid"*.
