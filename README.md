# TV Channel Watcher (tvchannel)

Δείχνει στο Home Assistant **ποιο κανάλι βλέπεις** στην TCL Android/Google TV και, όπου το υποστηρίζει το firmware, **τι πρόγραμμα παίζει τώρα** (τίτλος, ώρα έναρξης/λήξης, περιγραφή, είδος). Καταλαβαίνει αμέσως και τις αλλαγές από το τηλεχειριστήριο.

*Shows the currently tuned channel (and the current EPG programme where available) of a TCL Android/Google TV in Home Assistant, live, including changes made with the physical remote.*

## Δύο τρόποι λειτουργίας
| Mode | Πώς | Για ποια TV |
|---|---|---|
| **Live log stream** (προτείνεται) | Μόνιμη σύνδεση ADB που διαβάζει το log της TV τη στιγμή που αλλάζει κανάλι. Δίνει **όνομα καναλιού** + αισθητήρα **τρέχοντος προγράμματος**. | Firmware χωρίς τη ρύθμιση `recent_play_channel_id` |
| **Settings key** | Διαβάζει ανά 30 δευτερόλεπτα (ρυθμίζεται 10–600) τη ρύθμιση `recent_play_channel_id`. Δίνει **id καναλιού**. | Firmware που την έχει |

Στο live mode, αν περάσει ώρα χωρίς αλλαγή, η σύνδεση ανοίγει ξανά αθόρυβα και **κρατάει το τελευταίο κανάλι**. Ο αισθητήρας γίνεται «μη διαθέσιμος» μόνο όταν η TV δεν απαντάει.

## Εγκατάσταση (HACS)
1. **HACS → ⋮ → Custom repositories** → `https://github.com/stathisktm/ha-tvchannel`, τύπος **Integration**.
2. **TV Channel Watcher** → **Download** → επανεκκίνηση Home Assistant.
3. **Ρυθμίσεις → Συσκευές & υπηρεσίες → Προσθήκη → TV Channel Watcher**: διάλεξε το media_player της TV, το mode και (για live) την IP της TV.

Χρειάζεται το ενσωματωμένο integration **Android TV** (`androidtv`) ήδη ρυθμισμένο. Το live mode χρησιμοποιεί το ίδιο κλειδί ADB, οπότε δεν ζητάει νέα έγκριση στην TV.

## Φιλικό όνομα καναλιού
Ο αισθητήρας δίνει το όνομα/id όπως το γράφει η TV. Για δικό σου όνομα ή λογότυπο φτιάξε ένα template sensor πάνω του.

## Δείτε επίσης
- [ha-tvcam](https://github.com/stathisktm/ha-tvcam): κάμερα, μηνύματα και κινούμενα εικονίδια πάνω στην εικόνα της TV
- [ha-tvpicture](https://github.com/stathisktm/ha-tvpicture): mode εικόνας με μία εντολή

## Άδεια
[MIT](LICENSE)
