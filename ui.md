# OpenTrack-Manager UI Guide

## Product Direction

Build the app like a modern control center for uptime monitoring and security tools.

- Primary theme: dark, high-contrast, and polished
- Layout: desktop sidebar + top utility bar, mobile bottom navigation
- Feel: fast, calm, trustworthy, and operational
- Motion: subtle, practical transitions only
- Language: clear status labels and compact data density

## Brand Notes

- Product name: `OpenTrack-Manager`
- Core areas:
  - Monitors
  - Incidents
  - Vault
  - Authenticator
  - Public Status Page
  - Settings

## Information Architecture

### Desktop

- Left sidebar for primary navigation
- Main content area for tables, cards, and forms
- Top area for search, quick actions, and account actions

### Mobile

- Compact header with brand and status
- Bottom navigation for the main sections
- Content should stack vertically with large tap targets

## Visual Style

- Background: near-black with layered gradients
- Cards: glass-like panels with soft borders and blur
- Surfaces: slightly elevated, not flat
- Status colors:
  - Green for up or healthy
  - Red for down or danger
  - Amber for warning or attention
  - Blue for info or neutral actions
- Typography:
  - Strong hierarchy
  - Large numbers for health metrics
  - Small, readable labels for metadata

## Page-Level UI

### Dashboard

This is the main operational view.

- Show summary metrics at the top:
  - Total monitors
  - Online monitors
  - Down monitors
  - Incidents open
- Include a health score or operational score card
- Show recent monitor activity with response times and status
- Add quick actions:
  - New monitor
  - Open incidents
  - Vault
  - Authenticator
- Surface alerts and maintenance state clearly

### Monitors

The dashboard should support the monitor management workflow directly.

- List monitors in cards or a compact table
- Show:
  - Name
  - URL
  - Type
  - Status
  - Response time
  - Last check
  - Interval
  - Public/private state
- Support the monitor settings already in the backend:
  - HTTP method
  - Custom headers
  - Expected status code
  - Keyword check
  - Maintenance window
  - Tags
- Add a clear empty state when no monitors exist

### Incidents

Treat incidents like an audit trail.

- Show a chronological incident list
- Each incident should include:
  - Monitor name
  - Started time
  - Ended time if resolved
  - Duration
  - Root cause notes
- Use a strong visual distinction between open and resolved incidents
- Provide a resolve action with a compact note field

### Vault

This should feel like a secure password manager.

- Search and filter entries by name, URL, username, and category
- Show entries in grouped cards or a table
- For each item, support:
  - Name
  - URL
  - Username
  - Password reveal/copy
  - API key storage
  - Notes
  - Category
  - Password changed date
  - Favorite flag
- Make sensitive actions deliberate:
  - Copy
  - Reveal
  - Delete
- Use strong masking and confirmation states

### Authenticator

Design this like a focused OTP manager.

- Show tokens as stacked cards with:
  - Account name
  - Issuer
  - Category
  - Favorite indicator
  - Countdown ring or progress bar
  - Current OTP code
- Support:
  - Add secret manually
  - QR import
  - Favorite pinning
  - Search
  - Category grouping
- Codes should be easy to copy, but visually protected

### Public Status Page

This should be shareable and clean.

- Simple public-facing layout
- Show uptime status, counts, and monitor states
- Use large green/red status indicators
- Avoid admin controls on this page
- Keep branding minimal and white-label friendly

### Settings

Make settings easy to scan and save.

- Group notification settings by channel:
  - Telegram
  - Slack
  - Discord
  - Webhook
  - Email
- Show test buttons next to each integration
- Keep form sections separated with clear labels
- Use inline feedback after save/test actions

## Core Components

### Summary Cards

- Big number first
- Label second
- Small supporting delta or status text

### Tables

- Dense but readable
- Sticky headers on desktop if needed
- Status chips with consistent colors

### Forms

- Full-width inputs on mobile
- Two-column layout on desktop when practical
- Validation messages should sit directly under the field

### Empty States

- Explain what the user should do next
- Include one primary action
- Keep the copy short

### Alerts

- Use toast notifications for save/test actions
- Use persistent banners for operational warnings

## Motion

- Use quick fades and subtle slide transitions
- Animate card hover and button press states lightly
- Do not overuse bouncing, parallax, or decorative motion
- Status changes should feel immediate and grounded

## Accessibility

- Maintain strong color contrast
- Keep clickable areas large enough for mobile
- Do not rely on color alone for status
- Make keyboard navigation usable across forms and tables

## Practical Implementation Notes

- Reuse the existing routes and pages:
  - `/dashboard`
  - `/incidents`
  - `/vault`
  - `/authenticator`
  - `/status/{username}`
- Keep the UI consistent with the current FastAPI templates
- Prefer incremental improvements over a full visual rewrite
- Preserve the app's current operational workflow and data density

## Priority Order

1. Make the dashboard visually stronger and easier to scan
2. Improve monitor and incident readability
3. Refine vault and authenticator workflows
4. Polish mobile navigation and responsive layout
5. Add subtle motion and state feedback
