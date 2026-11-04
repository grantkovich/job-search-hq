// Resume template. All content arrives as JSON in sys.inputs.data and is placed as plain
// text, so nothing in it can run as Typst code.
#let d = json(bytes(sys.inputs.data))
#let accent = rgb("#1f4e79")

#set document(title: d.name + " - Resume", author: d.name)
#set page(paper: d.at("paper", default: "us-letter"), margin: (x: 0.7in, y: 0.6in))
#set text(font: "Libertinus Serif", size: 10.5pt, lang: "en")
#set par(justify: false, leading: 0.55em)

#let section(title) = {
  v(0.5em)
  text(size: 11.5pt, weight: "bold", fill: accent, upper(title))
  v(-0.6em)
  line(length: 100%, stroke: 0.6pt + accent)
  v(-0.2em)
}

#align(center)[
  #text(size: 20pt, weight: "bold", d.name) \
  #if d.at("headline", default: "") != "" [#text(size: 10.5pt, style: "italic", d.headline) \ ]
  #text(size: 9.5pt, d.contact.join("  |  "))
]

// One function per section; d.order says which come first (education leads for an early-career profile).
#let summary() = if d.summary != "" {
  section("Summary")
  d.summary
}

#let experience() = if d.experience.len() > 0 {
  section("Experience")
  for job in d.experience {
    block(breakable: false, above: 0.8em, below: 0.3em)[
      #grid(columns: (1fr, auto),
        [*#job.title* — #job.company],
        align(right, text(size: 9.5pt, job.dates)),
      )
      #if job.location != "" { text(size: 9.5pt, style: "italic", job.location) }
    ]
    for b in job.bullets {
      pad(left: 0.9em, bottom: 0.15em)[• #b]
    }
  }
}

#let education() = if d.education.len() > 0 {
  section("Education")
  for e in d.education {
    let name = if e.degree != "" [*#e.degree* — #e.school] else [*#e.school*]
    grid(columns: (1fr, auto), name, align(right, text(size: 9.5pt, e.dates)))
  }
}

#let certifications() = if d.at("certifications", default: ()).len() > 0 {
  section("Certifications & Licenses")
  for c in d.certifications {
    let name = if c.issuer != "" [*#c.name* — #c.issuer] else [*#c.name*]
    grid(columns: (1fr, auto), name, align(right, text(size: 9.5pt, c.date)))
  }
}

#let projects() = if d.at("projects", default: ()).len() > 0 {
  section("Projects")
  for p in d.projects {
    block(breakable: false, above: 0.8em, below: 0.4em)[
      #grid(columns: (1fr, auto), [*#p.name*], align(right, text(size: 9.5pt, p.date)))
      #if p.what != "" { pad(left: 0.9em, top: -0.3em, p.what) }
    ]
    v(0.2em)
  }
}

#let skills() = if d.skills.len() > 0 {
  section("Skills")
  d.skills.join(" · ")
}

#let sections = (summary: summary, experience: experience, education: education, certifications: certifications,
  projects: projects, skills: skills)
#for name in d.at("order", default: ("summary", "experience", "education", "certifications", "projects", "skills")) {
  if name in sections { sections.at(name)() }
}
