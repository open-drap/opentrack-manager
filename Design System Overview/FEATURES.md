# OpenTracker - Security Monitoring App

## Features Implemented

### 🔐 Authentication
- **Login Screen** - Username/password authentication
- **Register Screen** - Create new account with validation
- **Protected Routes** - Automatic redirect to login if not authenticated
- **Logout Functionality** - Available in Profile and Settings

### 🎨 Theme System
- **Dark Mode** - Professional dark theme with purple accents
- **Light Mode** - Clean light theme with adjusted colors
- **Theme Toggle** - Switch between dark/light mode in Settings
- **Persistent Theme** - Theme preference saved to localStorage
- **CSS Variables** - All colors use theme variables for easy customization

### 👤 Profile Page
- View and edit profile information
- Display security statistics
- Account status and member details
- Upgrade to Pro promotion
- Logout button

### ⚙️ Enhanced Settings
- **Account Section**
  - Profile management
  - Security settings
  - Biometric login toggle
  
- **Appearance Section**
  - Dark/Light theme toggle with visual switch
  - Language selection
  
- **Notifications Section**
  - Push notifications toggle
  - **Telegram Bot Integration**
    - Add bot token
    - Add chat ID
    - Setup instructions included
  - Auto-lock timer
  
- **Data Section**
  - Backup & Export
  - Import data
  - Storage usage display
  
- **Security Section**
  - Breach monitor

### 📱 Authenticator with QR Scanner
- **QR Code Scanning**
  - Camera access permission request
  - Real-time QR code scanning
  - html5-qrcode library integration
  - Scan authenticator QR codes
  
- **Add Account Options**
  - Scan QR Code (with camera)
  - Manual entry option
  
- **OTP Features**
  - 30-second countdown timer
  - Color-coded time remaining (red <5s)
  - Copy OTP codes
  - Pinned accounts in 2-column grid
  - Category filters (All/Work/Personal/Finance)
  - Search functionality

### 📊 Dashboard
- Security score with circular progress
- 2x2 statistics grid
- Smart alerts with priority levels
- Good morning greeting with avatar
- Bottom navigation

### 🖥️ Monitors
- Real-time monitor status
- Uptime percentage tracking
- SSL certificate expiry days
- Ping response time
- Color-coded metrics (green/amber/red)
- Filter by All/Up/Down
- Country flags
- Add new monitor button

### 🔒 Vault
- Password list with strength indicators
- Show/hide password toggle
- Copy password functionality
- Password warnings (old, no 2FA)
- Category filters
- **Password Generator**
  - Adjustable length (8-32 characters)
  - Toggle symbols, numbers, uppercase
  - Real-time generation
  - Copy generated password
  - Bottom sheet modal

### 📜 Activity Timeline
- Chronological activity feed
- Color-coded event types
  - 🔐 Auth (purple)
  - 🖥️ Monitor (cyan)
  - 🔑 Vault (green)
  - ⚠️ Alert (red)
- Grouped by date (Today/Yesterday)
- AI Security Audit promotion

### 🎨 Color Palette

#### Dark Theme
- Background: `#0A0A0F`
- Card: `#111118`
- Card Surface: `#1A1A2E`
- Primary: `#7C6AFF` (Purple)
- Success: `#22C55E` (Green)
- Warning: `#F59E0B` (Amber)
- Destructive: `#EF4444` (Red)
- Gradient End: `#06B6D4` (Cyan)
- Text: `#E2E2F0`
- Muted: `#4A4A6A`

#### Light Theme
- Background: `#F5F5FA`
- Card: `#FFFFFF`
- Card Surface: `#E8E8F0`
- Primary: `#6B5CE5` (Purple)
- Success: `#16A34A` (Green)
- Warning: `#D97706` (Amber)
- Destructive: `#DC2626` (Red)
- Gradient End: `#0891B2` (Cyan)
- Text: `#1A1A2E`
- Muted: `#8A8AA0`

### 📦 Installed Packages
- `react-router` - For navigation and routing
- `html5-qrcode` - QR code scanning functionality
- `@zxing/library` - Barcode/QR decoding
- `lucide-react` - Icon library
- `motion` - Animations (if needed)

### 🔄 Data Persistence
- User authentication state (localStorage)
- User profile (name, email)
- Theme preference
- Telegram bot credentials
- All data survives page refreshes

### 🚀 How to Use

1. **First Time Setup**
   - Navigate to `/register` or click "Sign Up"
   - Enter your details
   - Automatically logged in after registration

2. **Login**
   - Navigate to `/login`
   - Enter email and password
   - Stay logged in across sessions

3. **Theme Toggle**
   - Go to Settings
   - Find "Theme" under Appearance
   - Toggle between Dark/Light mode

4. **Telegram Integration**
   - Go to Settings
   - Click "Telegram Alerts"
   - Follow the modal instructions:
     1. Create bot with @BotFather
     2. Get chat ID from @userinfobot
     3. Save credentials

5. **Add Authenticator Account**
   - Go to Authenticator tab
   - Click the "+" button
   - Choose "Scan QR Code"
   - Grant camera permission
   - Point camera at QR code

6. **View Profile**
   - Go to Settings
   - Click "Profile" at the top
   - Or access from navigation

### 🎯 Next Steps / Future Enhancements
- Implement actual Telegram API integration
- Add biometric authentication (fingerprint/face ID)
- Real monitor API connections
- Password strength checker
- Breach monitoring integration
- Export/Import functionality
- Multi-language support
- Push notifications
- 2FA setup wizard
- Password auto-fill
