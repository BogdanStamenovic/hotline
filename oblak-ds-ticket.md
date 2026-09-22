# Oblak support ticket — DS record for kinreply.rs

**Status: SENT 2026-09-22 19:00:27 CEST.** Owner: the hotline operator, reassigned from
Bogdan on 2026-09-22.

    Sep 22 19:00:27 host=smtp.gmail.com tls=on auth=on user=kinreply@gmail.com
      from=kinreply@gmail.com recipients=info@oblak.host mailsize=1182
      smtpstatus=250 smtpmsg='250 2.0.0 OK ...' exitcode=EX_OK

**What that proves and what it does not.** It proves Gmail accepted the message from the
registrant address — which was the whole requirement. It does **not** prove Oblak received
it, read it, or will act on it. A 250 is acceptance by the sending relay, the same distinction
that cost a day on the Instagram DMs. The answer is a reply from Oblak or a DS appearing at
`.rs`, nothing short of it.

**Recipient:** `info@oblak.host`, the only address published on `oblak.host` (their panel at
`moj.oblak.host` is the other support route). If no reply comes in a few days, the panel
ticket is the fallback and it is authenticated, which for a registrant-verification request
is arguably the stronger channel.

## The blocker, precisely — RESOLVED 2026-09-22 18:59

It must be sent **from `kinreply@gmail.com`** — Bogdan's instruction, because that is the
address the domain was bought with and Oblak acts on the registrant contact. A ticket from
any other address is **ignored, not bounced**, so a wrong sender fails silently.

| Credential | Sends as | Satisfies the requirement? |
|---|---|---|
| The existing `~/.msmtprc` `gmail` account | `bogdan.stamenovic@gmail.com` | **No** — different account |
| Chunk 7's Resend `sending_access` key | `support@kinreply.rs` | **No** — different sender; that key lets Gmail send *as* the alias, it does not authenticate *as* `kinreply@gmail.com` |
| **A Google App Password for `kinreply@gmail.com`** | `kinreply@gmail.com` | **Yes** — this is the one needed |

Bogdan creates it at `myaccount.google.com` → Security → App passwords (needs 2FA on that
account). 16 characters. **No trailing newline** when it is stored — the same trap chunk 7
documents for the Gmail paste, where a newline is accepted at setup and then fails at send
time with no bounce and no record anywhere.

When it arrives: add an `msmtp` account `kinreply` (`smtp.gmail.com`, port 587, TLS,
user and from `kinreply@gmail.com`), then send. Independent of chunk 7 and available as
soon as he makes it.

## The DS values — recomputed here, not copied

Read from the live DNSKEY at `ns1.desec.io` and recomputed with `dnssec-dsfromkey`, rather
than transcribed from link 1's report. Both digests match its values exactly. One DNSKEY is
published, flags 257, algorithm 13.

    SHA-256 (digest type 2)
    37955 13 2 68C48D50EF59D8BC45746D180954079A87D6EAEE1114B6EB78C2220CEB767E10

    SHA-384 (digest type 4)
    37955 13 4 AB62960AAB89B53B2E1123370EFD94C1353FEB5FA988B5C8632ACA07E8AA5EFAF34BA39FBA941C3D4BDC2073591FAB95

Re-derive before sending, because a rotated KSK makes these stale:

    dig +short +nocookie @ns1.desec.io kinreply.rs DNSKEY \
      | awk '{print "kinreply.rs. 3600 IN DNSKEY " $0}' > /tmp/k.zone
    dnssec-dsfromkey -2 -f /tmp/k.zone kinreply.rs

## Draft — Serbian, ASCII without diacritics (his standing rule for outward text)

> **To:** Oblak support (`moj.oblak.host`)
> **From:** kinreply@gmail.com
> **Subject:** kinreply.rs — slanje DS zapisa ka RNIDS-u (DNSSEC)
>
> Postovani,
>
> Domen kinreply.rs je registrovan preko vas, na ovu email adresu.
>
> Zona je potpisana (DNSSEC) i hostovana je kod deSEC-a. U vasem korisnickom panelu ne
> postoji polje za DNSSEC, pa vas molim da DS zapis posaljete RNIDS-u u nase ime.
>
> DS zapis (SHA-256):
>
>     37955 13 2 68C48D50EF59D8BC45746D180954079A87D6EAEE1114B6EB78C2220CEB767E10
>
> Ako vam je potreban SHA-384 digest umesto toga:
>
>     37955 13 4 AB62960AAB89B53B2E1123370EFD94C1353FEB5FA988B5C8632ACA07E8AA5EFAF34BA39FBA941C3D4BDC2073591FAB95
>
> Ako vam je potreban DNSKEY umesto DS zapisa: flags 257, protocol 3, algorithm 13, a javni
> kljuc se moze procitati sa `dig +short @ns1.desec.io kinreply.rs DNSKEY`.
>
> Nameserveri su ns1.desec.io i ns2.desec.org i vec su aktivni.
>
> Hvala unapred,
> Bogdan Stamenovic

English version available on request; the technical values are identical.

## How to verify it landed

    curl -sS 'https://dns.google/resolve?name=api.kinreply.rs&type=A'   # expect "AD":true
    delv @1.1.1.1 api.kinreply.rs A                                     # expect "fully validated"

Today both report the unsigned state, which is correct and harmless: the zone is signed but
there is no DS at the `.rs` parent, so validators get a proof that none exists.

## The warning that must travel with this, forever

**If DNS ever moves away from deSEC, remove the DS at the registrar FIRST and wait out its
TTL.** Otherwise every validating resolver fails the domain closed and kinreply.rs goes dark
— not slow, not intermittent, dark. This is the one irreversible-feeling consequence of
submitting the DS at all, and it is why the item carries a warning rather than just a value.
