import type React from "react";
import { ArrowRight, Check } from "lucide-react";
import "@fontsource/instrument-serif/400.css";
import "@fontsource/instrument-serif/400-italic.css";
import { Button } from "../components/ui/button";
import { CREATE_ACCOUNT_PATH, SIGN_IN_PATH } from "./landingRoute";
import listingPhoto from "./assets/listing-example.webp";
import "./landing.css";

const MARKETPLACES = ["eBay", "Poshmark", "Mercari", "Depop", "Etsy"];

const STEPS = [
  ["Add your photos", "Add your photos once. They go out with the listing to every marketplace you sell on."],
  ["Write it once", "Title, description, price, condition and size all live in one listing. Save it as a draft and finish it later."],
  ["Fill every marketplace", "Anteroom fills in each marketplace’s listing form. You check it over and hit publish."],
];

const APPS = [
  ["iOS", "Snap photos and start a listing on your iPhone, right where the item is."],
  ["Web", "Open Anteroom in any browser. There is nothing to install."],
  ["Desktop", "Work through a whole batch on a big screen with the desktop app."],
];

// Vertical centre of each marketplace row, as a percent of the list height (52px rows, 8px gaps).
const LIST_HEIGHT = MARKETPLACES.length * 52 + (MARKETPLACES.length - 1) * 8;
const rowCentre = (index: number) => ((26 + index * 60) / LIST_HEIGHT) * 100;

function CrossList() {
  return (
    <figure className="landing-crosslist">
      <div className="landing-crosslist-frame">
        <article className="landing-listing" aria-label="Example listing">
          <img src={listingPhoto} width={880} height={880} alt="Red Coldwater Creek jacquard button-up blouse on a mannequin" fetchPriority="high" />
          <div className="landing-listing-body">
            <p className="landing-listing-title">Coldwater Creek Jacquard Button-Up Blouse, Red</p>
            <p className="landing-listing-meta">Women’s S · Pre-owned</p>
            <p className="landing-listing-price">$17</p>
          </div>
        </article>
        <svg className="landing-crosslist-lines" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
          {MARKETPLACES.map((name, index) => <path key={name} d={`M0 50 C 50 50, 50 ${rowCentre(index)}, 100 ${rowCentre(index)}`} />)}
        </svg>
        <ul className="landing-markets" aria-label="Marketplaces filled from this listing">
          {MARKETPLACES.map((name, index) => (
            <li key={name} style={{ "--i": index } as React.CSSProperties}>
              <span className="landing-market-name">{name}</span>
              <span className="landing-market-status"><Check aria-hidden="true" /> Filled</span>
            </li>
          ))}
        </ul>
      </div>
      <figcaption>One listing fills five marketplace forms. You review and publish each.</figcaption>
    </figure>
  );
}

/** What a signed-out visitor sees at myanteroom.com. */
export function Landing() {
  return (
    <div className="landing">
      <a className="landing-skip" href="#landing-main">Skip to content</a>
      <header className="landing-nav">
        <a className="landing-brand" href="/">Anteroom</a>
        <nav aria-label="Main">
          <a className="landing-nav-link" href="#how">How it works</a>
          <a className="landing-nav-link" href="#apps">Apps</a>
          <Button variant="ghost" render={<a href={SIGN_IN_PATH} />}>Sign in</Button>
        </nav>
      </header>

      <main id="landing-main">
        <section className="landing-hero">
          <h1>One listing. <em>Every storefront.</em></h1>
          <p className="landing-lede">Write a listing once and Anteroom fills it in on eBay, Poshmark, Mercari, Depop and Etsy. You review each one and hit publish.</p>
          <div className="landing-cta-row">
            <Button size="xl" render={<a href={CREATE_ACCOUNT_PATH} />}>Create your account</Button>
            <a className="landing-text-link" href="#how">See how it works <ArrowRight aria-hidden="true" /></a>
          </div>
          <p className="landing-assurance">Web, iOS and desktop · You press publish</p>
          <CrossList />
        </section>

        <section id="how" className="landing-section" aria-labelledby="how-title">
          <h2 id="how-title">Write it once. Anteroom handles the copy and paste.</h2>
          <ol className="landing-columns">
            {STEPS.map(([title, body], index) => (
              <li key={title}>
                <span className="landing-step-index" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
                <h3>{title}</h3>
                <p>{body}</p>
              </li>
            ))}
          </ol>
        </section>

        <section id="apps" className="landing-section" aria-labelledby="apps-title">
          <h2 id="apps-title">List from wherever the item is.</h2>
          <ul className="landing-columns">
            {APPS.map(([title, body]) => <li key={title}><h3>{title}</h3><p>{body}</p></li>)}
          </ul>
        </section>

        <section className="landing-closing" aria-labelledby="closing-title">
          <h2 id="closing-title">List it once.</h2>
          <p>Then get back to sourcing.</p>
          <Button size="xl" render={<a href={CREATE_ACCOUNT_PATH} />}>Create your account</Button>
        </section>
      </main>

      <footer className="landing-footer">
        <span className="landing-brand">Anteroom</span>
        <span>Web · iOS · Desktop</span>
      </footer>
    </div>
  );
}
