# Scene Template Library: Organized by Output Type

> Use in combination with the "Prompt DNA" from design-styles.md.
> Formula: `[Style Prompt DNA] + [Scene Template] + [Specific Content Description]`

---

## 1. WeChat Official Account Cover / Article Header Image

**Specs**:

- Cover image: 2.35:1 (900x383px or 1200x510px)
- In-article illustration: 16:9 (1200x675px) or 4:3 (1200x900px)

**Key Design Considerations**:

- Visual impact is the priority (users scan quickly through their feed)
- Minimal or no text (the article title will be overlaid on top)
- Moderate color saturation (WeChat reading environment has a white background)
- Avoid excessive detail (must remain recognizable as a thumbnail)

**Recommended styles**: 01 Pentagram / 11 Build / 12 Sagmeister / 18 Kenya Hara / 07 Field.io

**Scene Prompt Template**:

```
[Insert Style DNA here]
- Article cover image for WeChat subscription
- Landscape format, 2.35:1 aspect ratio
- Bold visual impact, minimal or no text
- Moderate color saturation for white reading environment
- Must remain recognizable as thumbnail
- Clean composition with clear focal point
```

---

## 2. In-Article Illustration / Concept Art

**Specs**:

- 16:9 (1200x675px) — most versatile
- 1:1 (800x800px) — good for emphasis
- 4:3 (1200x900px) — good for information-dense layouts

**Key Design Considerations**:

- Serves the article's argument, not decoration
- Creates visual rhythm alongside the surrounding content
- Communicates one clear core concept
- AI generation preferred; HTML screenshots only for precise data tables

**Recommended styles**: Choose based on article tone — commonly 01/04/10/17/18

**Scene Prompt Template**:

```
[Insert Style DNA here]
- Article illustration, concept visualization
- [16:9 / 1:1 / 4:3] aspect ratio
- Single clear concept: [describe the core concept]
- Serve the argument, not decoration
- [Light/Dark] background to match article tone
```

---

## 3. Infographic / Data Visualization

**Specs**:

- Tall vertical: 1080x1920px (mobile reading)
- Landscape: 1920x1080px (embedded in articles)
- Square: 1080x1080px (social media)

**Key Design Considerations**:

- Clear information hierarchy (title -> key data -> details)
- Accurate data, no fabrication
- Visual flow guides the reader's eye path
- Appropriate use of icons/charts to aid comprehension

**Recommended styles**: 04 Fathom / 10 Muller-Brockmann / 02 Stamen / 17 Takram

**Scene Prompt Template**:

```
[Insert Style DNA here]
- Infographic / data visualization
- [Vertical 1080x1920 / Horizontal 1920x1080 / Square 1080x1080]
- Clear information hierarchy: title -> key data -> details
- Visual flow guiding reader's eye path
- Icons and charts for comprehension
- Data-accurate, no decorative distortion
```

---

## 4. PPT / Keynote Presentation

**Specs**:

- Standard: 16:9 (1920x1080px)
- Widescreen: 16:10 (1920x1200px)

**Key Design Considerations**:

- One core message per slide (no piling on)
- Clear type size hierarchy (title 40pt+ / body 24pt+ / notes 16pt+)
- Generous whitespace — much clearer when projected
- Image-to-text ratio at least 60:40
- Consistent visual system (colors, fonts, spacing)

**Recommended styles**: 01 Pentagram / 10 Muller-Brockmann / 11 Build / 18 Kenya Hara / 04 Fathom

**Scene Prompt Template**:

```
[Insert Style DNA here]
- Presentation slide design, 16:9
- One core message per slide
- Clear type hierarchy (title 40pt+, body 24pt+)
- Generous whitespace for projection clarity
- Consistent visual system throughout
- [Light/Dark] theme
```

---

## 5. PDF White Paper / Technical Report

**Specs**:

- A4 portrait (210x297mm / 595x842pt)
- Letter portrait (216x279mm / 612x792pt)

**Key Design Considerations**:

- Optimized for long-form reading (66-character line width, 1.5-1.8 line height)
- Clear chapter navigation system
- Consistent header/footer/page number design
- Elegant integration of figures and body text
- Citation/footnote system
- Refined cover page design

**Recommended styles**: 10 Muller-Brockmann / 04 Fathom / 03 Information Architects / 17 Takram / 19 Irma Boom

**Scene Prompt Template**:

```
[Insert Style DNA here]
- PDF document / white paper design
- A4 portrait format (210x297mm)
- Long-form reading optimized (66 char line width, 1.5 line height)
- Clear chapter navigation system
- Elegant header/footer/page number design
- Charts integrated with body text
- Professional cover page
```

---

## 6. Landing Page / Product Website

**Specs**:

- Desktop: designed at 1440px width (responsive down to 320px)
- Hero section height: 100vh

**Key Design Considerations**:

- Communicate core value within 5 seconds of the hero section
- Clear CTA (call-to-action button)
- Scroll narrative structure (problem -> solution -> proof -> action)
- Mobile-responsive
- Load speed

**Recommended styles**: 05 Locomotive / 01 Pentagram / 11 Build / 08 Resn / 06 Active Theory

**Scene Prompt Template**:

```
[Insert Style DNA here]
- Landing page / product website
- Desktop 1440px width, responsive
- Hero section 100vh, core value in 5 seconds
- Clear CTA button design
- Scroll narrative: problem -> solution -> proof -> action
- Modern web aesthetic
```

---

## 7. App UI / Prototype Interface

**Specs**:

- iOS: 390x844pt (iPhone 15)
- Android: 360x800dp
- Tablet: 1024x1366pt (iPad Pro)

**Key Design Considerations**:

- Touch-friendly (minimum tap target 44x44pt)
- Consistent design system language
- Standard handling of status bar/navigation bar/tab bar
- Moderate information density (mobile should not be overly dense)

**Recommended styles**: 17 Takram / 11 Build / 03 Information Architects / 01 Pentagram

**Scene Prompt Template**:

```
[Insert Style DNA here]
- Mobile app UI design
- iOS [390x844pt] / Android [360x800dp]
- Touch-friendly (44pt minimum tap targets)
- Consistent design system
- Standard status bar / navigation / tab bar
- Moderate information density
```

---

## 8. Xiaohongshu (RED) Images

**Specs**:

- Vertical: 3:4 (1080x1440px) — optimal
- Square: 1:1 (1080x1080px)
- The first image determines click-through rate

**Key Design Considerations**:

- Visual appeal is the top priority (competing in a waterfall feed)
- Can include a small amount of text (no more than 20% of the frame)
- Vivid colors that are tasteful, not gaudy
- Lifestyle feel / texture / atmosphere

**Recommended styles**: 12 Sagmeister / 11 Build / 20 Neo Shen / 09 Experimental Jetset

**Scene Prompt Template**:

```
[Insert Style DNA here]
- Social media image for Xiaohongshu (RED)
- Vertical 3:4 (1080x1440px)
- Eye-catching in waterfall feed
- Minimal text overlay (under 20% of area)
- Vivid but tasteful colors
- Lifestyle/texture/atmosphere feel
```

---

## Combination Example

**Scenario**: WeChat official account cover, introducing an AI coding tool — want it professional but warm

**Step 1**: Choose style -> 17 Takram (professional + warmth)
**Step 2**: Combine Takram prompt DNA + WeChat cover template

```
Takram Japanese speculative design:
- Elegant concept prototypes and diagrams
- Soft tech aesthetic (rounded corners, gentle shadows)
- Charts and diagrams as art pieces
- Modest sophistication
- Neutral natural colors (beige, soft gray, muted green)
- Design as philosophical inquiry

Article cover image for WeChat subscription
- Landscape format, 2.35:1 aspect ratio (1200x510px)
- Bold visual impact, minimal text
- Moderate color saturation for white reading environment
- Must remain recognizable as thumbnail
- Clean composition with clear focal point

Content: An AI coding assistant tool, showing the concept of human-AI collaboration
in software development, warm and professional atmosphere
```

---

**Version**: v1.0
**Updated**: 2026-02-13
