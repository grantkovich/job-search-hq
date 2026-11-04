// Cover letter template. Content arrives as JSON in sys.inputs.data and is placed as plain text.
#let d = json(bytes(sys.inputs.data))

#set document(title: d.name + " - Cover letter", author: d.name)
#set page(paper: d.at("paper", default: "us-letter"), margin: (x: 1in, y: 0.9in))
#set text(font: "Libertinus Serif", size: 11pt, lang: "en")
#set par(justify: false, leading: 0.65em, spacing: 1.1em)

#text(size: 16pt, weight: "bold", d.name) \
#text(size: 9.5pt, d.contact.join("  |  "))

#v(1.2em)
#d.date

#v(0.6em)
#d.company

#v(0.6em)
#d.greeting

#for p in d.paragraphs [
  #p

]

#d.closing \
#v(1.6em)
#d.name
