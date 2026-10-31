import Link from "next/link";
import { AskDemo } from "@/components/AskDemo";
import { Wordmark } from "@/components/Brand";
import { Closing } from "@/components/Closing";
import { Gallery } from "@/components/Gallery";
import { HeroReel } from "@/components/HeroReel";
import { Opening } from "@/components/Opening";
import { Overlap } from "@/components/Overlap";
import { Reveal } from "@/components/Reveal";
import { Stages } from "@/components/Stages";
import { ThemeToggle } from "@/components/ThemeToggle";
import { readSession } from "@/lib/auth/session";

const SIGN_UP = "/api/auth/login?mode=signup&returnTo=/continue";
const SIGN_IN = "/api/auth/login?mode=signin&returnTo=/continue";

const FAQ = [
  { q: "What kinds of documents can it read?", a: "Receipts, invoices, order forms, delivery notes, application forms, handwritten notes and the odd ones only your business sees. Today they need to be photographs or scans saved as JPG, PNG, TIFF or WebP images. PDFs aren't supported yet." },
  { q: "What happens when Syruss isn't sure?", a: "It asks a person and keeps the answer, so the next document of that kind is checked against it. It never guesses a value and passes it on quietly." },
  { q: "Can I change what it captures later?", a: "Yes. Change the list once and new documents follow it. Syruss proposes the change and you approve it." },
  { q: "Where does the data go?", a: "Wherever you point it: the systems your team already uses. Everything is also kept in Syruss, next to the file it came from." },
  { q: "Who can see my documents?", a: "Only the people you add to a workspace, each with a role you choose. Your questions to Syruss are visible only to you." },
  { q: "How do I invite my team?", a: "Send them an invitation link, or let Syruss email it. It is for one email address, works once and expires after a week." },
  { q: "Can I sign in with Google?", a: "Yes, or with an email and password. A new browser asks for a code we email you, and you can add two-step verification too." },
];

export default async function Home({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const { error } = await searchParams;
  let signedIn = false;
  try {
    signedIn = (await readSession()) !== null;
  } catch {
    signedIn = false;
  }
  const primary = signedIn ? "/continue" : SIGN_UP;

  return (
    <>
      <Reveal />
      <header className="nav">
        <Link href="/" className="nav-brand" aria-label="Syruss, home"><Wordmark /></Link>
        <nav aria-label="Main" className="nav-links">
          <a href="#how-it-works">How it works</a>
          <a href="#documents">Documents</a>
          <a href="#ask">Ask Syruss</a>
          <a href="#faq">Questions</a>
          {signedIn ? null : <a href={SIGN_IN}>Sign in</a>}
        </nav>
        <ThemeToggle />
        <a className="button button-small" href={signedIn ? "/continue" : SIGN_UP}>{signedIn ? "Open Syruss" : "Create account"}</a>
      </header>

      {error ? (
        <p className="notice" role="alert">
          {error === "expired" ? "That sign-in took too long. Please try again." : "We couldn't sign you in. Please try again."}
        </p>
      ) : null}

      <main>
        <HeroReel>
          <h1 className="hero-sentence">
            <span className="hero-left">Drop the page.</span>
            <span className="hero-right">Data takes the stage.</span>
          </h1>
          <div className="hero-support">
            <p>Syruss reads your receipts, invoices, forms and any other documents, keeps every file safe, and sends the details to the systems you already use.</p>
            <div className="hero-actions">
              <a className="button" href={primary}>{signedIn ? "Open Syruss" : "Create your account"}</a>
              {signedIn ? null : <a className="link-quiet" href={SIGN_IN}>Sign in</a>}
            </div>
          </div>
        </HeroReel>

        <Stages />

        <Gallery
          id="any-h"
          title="Crumpled or neat, read complete."
          text="Receipts, invoices, order forms, delivery notes, application forms, and the odd ones only your business sees. Photographs and scans, tidy or crumpled. You don’t need a different tool for each kind."
          names={["receipts", "invoice-paris", "ledger", "receipt-lyon", "invoice-new-york", "savings-book", "invoice-london"]}
        />

        <Opening
          id="messy-h"
          title="Stamps and smudges? No grudges."
          text="Columns, handwriting, stamps and smudges. Paperwork rarely looks the same twice, so when Syruss can’t be sure of something it asks you."
          photo="savings-book"
        />

        <Overlap
          panels={[
            {
              id: "publish-h",
              anchor: "publish",
              title: "Sent on the day, on its way.",
              text: "When a document has been read and checked, its details are sent to the other systems your team uses, so nobody retypes anything. You choose what is sent and where, and you can see what was sent and when.",
              photo: "cargo",
            },
            {
              id: "cloud-h",
              anchor: "cloud",
              title: "Filed with care, found right there.",
              text: "Each file is saved in Syruss next to the details taken from it. Open the original any time, see exactly what was read from it, and see who checked it. Nothing lives in an inbox or on someone’s desktop.",
              photo: "card-drawer",
            },
          ]}
        />

        <AskDemo />

        <section className="faq" id="faq" aria-labelledby="faq-h">
          <div className="faq-side">
            <h2 id="faq-h">Questions</h2>
            <p>What people ask before they sign up, answered in full.</p>
          </div>
          <div className="faq-list">
            {FAQ.map((f) => (
              <div className="faq-row" key={f.q} data-reveal>
                <h3 className="faq-q">{f.q}</h3>
                <p className="faq-a">{f.a}</p>
              </div>
            ))}
          </div>
        </section>

        <Closing primary={primary} signedIn={signedIn} signIn={SIGN_IN} />
      </main>

      <footer className="footer">
        <div className="footer-brand">
          <Link href="/" aria-label="Syruss, home"><Wordmark /></Link>
          <p>Drop the page. Data takes the stage.</p>
        </div>
        <nav className="footer-cols" aria-label="Footer">
          <ul>
            <li><a href="#how-it-works">How it works</a></li>
            <li><a href="#documents">Documents</a></li>
            <li><a href="#ask">Ask Syruss</a></li>
            <li><a href="#faq">Questions</a></li>
          </ul>
          <ul>
            <li><a href={SIGN_IN}>Sign in</a></li>
            <li><a href={SIGN_IN + "&idp=google"}>Continue with Google</a></li>
            <li><a href={SIGN_UP}>Create an account</a></li>
          </ul>
        </nav>
        <p className="footer-legal"><span>© Syruss</span></p>
      </footer>
    </>
  );
}
