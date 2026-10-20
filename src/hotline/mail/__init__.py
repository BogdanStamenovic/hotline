"""hotline-mail: read Bogdan's inbox, and keep a board of mail somebody is waiting for.

Two halves, asked for together on 2026-10-20 ("you sign up to something or
expect some kind of email ... check the email ... and post up expected email on
a notice board, so you know what you have and have not received yet"):

* `inbox`  -- a READ-ONLY IMAP view of his Gmail: recent, search, show.
* `board`  -- expectations ("a mail from /skyfall/ should arrive by Friday"),
  matched against new mail every few minutes by hotlined, shown on one pinned
  Discord message that is edited in place, and announced to the operator.

Mail goes through the operator and nobody else: his words, same night, "email
go[es] through you. So hotline-80 only gets notified". Other agents ask the
operator; they do not read the inbox, and they are never notified directly.

Everything that came out of an email is untrusted data, and every path that
shows it to a model wraps it in `untrusted.fence`. See that module.
"""
