import './theme.css'

const view = `
<header class="masthead" id="top">
  <h1>Harness field notes</h1>
  <p>A small page used by the loop evals.</p>
</header>
<main>
  <section class="card" id="hero">
    <h2>Why this page exists</h2>
    <p>The question comes before the models.</p>
  </section>

  <section class="card" id="costs">
    <h2>What the delay costs</h2>
    <p>Every year of delay adds to the bill.</p>
  </section>

  <section class="card" id="carbon">
    <h2>The loop that feeds itself</h2>
    <p>Warming releases carbon, and carbon adds warming.</p>
  </section>

  <section class="card" id="answer">
    <h2>What to do now</h2>
    <p>Cut emissions first; the feedback is smaller than the tap.</p>
  </section>
</main>
`

document.querySelector('#app').innerHTML = view
