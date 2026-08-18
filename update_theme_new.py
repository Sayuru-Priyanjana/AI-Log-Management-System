import re

with open('ui/src/index.css', 'r', encoding='utf-8') as f:
    content = f.read()

light_theme = """
:root {
  --bg-color: #fdfffc;
  --bg-color-raised: #fefffd;

  --text-primary: #011627;
  --text-secondary: #034a83;
  --text-tertiary: #067ddf;

  --accent-color: #2ec4b6;
  --accent-2: #ff9f1c;
  --accent-hover: #50d6c9;
  --accent-soft: rgba(46, 196, 182, 0.1);
  --accent-glow: rgba(46, 196, 182, 0.3);
  --accent-gradient: linear-gradient(135deg, #2ec4b6 0%, #ff9f1c 100%);

  --glass-bg: rgba(253, 255, 252, 0.65);
  --glass-bg-soft: rgba(253, 255, 252, 0.4);
  --glass-bg-solid: #fdfffc;
  --glass-border: #a4d5fd;
  --glass-highlight: rgba(1, 22, 39, 0.15);

  --success: #2ec4b6;
  --success-bg: rgba(46, 196, 182, 0.1);
  --success-border: rgba(46, 196, 182, 0.25);
  --error: #e71d36;
  --error-bg: rgba(231, 29, 54, 0.1);
  --error-border: rgba(231, 29, 54, 0.25);
  --warning: #ff9f1c;
  --warning-bg: rgba(255, 159, 28, 0.1);
  --warning-border: rgba(255, 159, 28, 0.25);

  --violet: #067ddf;
  --violet-bg: rgba(6, 125, 223, 0.1);
  --violet-border: rgba(6, 125, 223, 0.25);

  --r-sm: 8px;
  --r-md: 12px;
  --r-lg: 16px;
  --r-xl: 20px;
  --r-full: 999px;

  --shadow-sm: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03);
  --shadow-md: 0 10px 15px -3px rgba(0, 0, 0, 0.05), 0 4px 6px -2px rgba(0, 0, 0, 0.03);
  --shadow-lg: 0 20px 25px -5px rgba(0, 0, 0, 0.05), 0 10px 10px -5px rgba(0, 0, 0, 0.02);
  --shadow-glow: 0 0 15px var(--accent-glow);

  --ease: cubic-bezier(0.4, 0, 0.2, 1);
  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
}
""".strip()

dark_theme = """
:root[data-theme='dark'] {
  --bg-color: #010d18;
  --bg-color-raised: #011627;

  --text-primary: #fdfffc;
  --text-secondary: #a4d5fd;
  --text-tertiary: #48aafa;

  --accent-color: #2ec4b6;
  --accent-2: #ff9f1c;
  --accent-hover: #50d6c9;
  --accent-soft: rgba(46, 196, 182, 0.15);
  --accent-glow: rgba(46, 196, 182, 0.4);
  --accent-gradient: linear-gradient(135deg, #2ec4b6 0%, #ff9f1c 100%);

  --glass-bg: rgba(1, 22, 39, 0.65);
  --glass-bg-soft: rgba(1, 13, 24, 0.4);
  --glass-bg-solid: #011220;
  --glass-border: #034a83;
  --glass-highlight: rgba(253, 255, 252, 0.15);

  --success: #2ec4b6;
  --success-bg: rgba(46, 196, 182, 0.1);
  --success-border: rgba(46, 196, 182, 0.2);
  --error: #e71d36;
  --error-bg: rgba(231, 29, 54, 0.1);
  --error-border: rgba(231, 29, 54, 0.2);
  --warning: #ff9f1c;
  --warning-bg: rgba(255, 159, 28, 0.1);
  --warning-border: rgba(255, 159, 28, 0.2);

  --violet: #48aafa;
  --violet-bg: rgba(72, 170, 250, 0.1);
  --violet-border: rgba(72, 170, 250, 0.2);

  --shadow-sm: 0 4px 6px -1px rgba(0, 0, 0, 0.3), 0 2px 4px -1px rgba(0, 0, 0, 0.2);
  --shadow-md: 0 10px 15px -3px rgba(0, 0, 0, 0.4), 0 4px 6px -2px rgba(0, 0, 0, 0.2);
  --shadow-lg: 0 20px 25px -5px rgba(0, 0, 0, 0.5), 0 10px 10px -5px rgba(0, 0, 0, 0.3);
}
""".strip()

bg_mesh_css = """
.bg-mesh { 
  display: block; 
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  z-index: -1;
  background-image: 
    radial-gradient(at 0% 0%, rgba(46, 196, 182, 0.15) 0px, transparent 50%),
    radial-gradient(at 100% 0%, rgba(255, 159, 28, 0.15) 0px, transparent 50%),
    radial-gradient(at 100% 100%, rgba(231, 29, 54, 0.1) 0px, transparent 50%),
    radial-gradient(at 0% 100%, rgba(6, 125, 223, 0.05) 0px, transparent 50%);
  filter: blur(60px);
  animation: pulseMesh 15s ease-in-out infinite alternate;
}
:root[data-theme='dark'] .bg-mesh {
  background-image: 
    radial-gradient(at 0% 0%, rgba(46, 196, 182, 0.12) 0px, transparent 50%),
    radial-gradient(at 100% 0%, rgba(255, 159, 28, 0.12) 0px, transparent 50%),
    radial-gradient(at 100% 100%, rgba(231, 29, 54, 0.08) 0px, transparent 50%),
    radial-gradient(at 0% 100%, rgba(72, 170, 250, 0.05) 0px, transparent 50%);
}
""".strip()

content = re.sub(r':root\s*\{[^}]*--ease-out:[^}]*\}', light_theme, content, flags=re.DOTALL)
content = re.sub(r':root\[data-theme=\'dark\'\]\s*\{[^}]*\}', dark_theme, content, flags=re.DOTALL)
content = re.sub(r'\.bg-mesh\s*\{.*?:root\[data-theme=\'dark\'\]\s*\.bg-mesh\s*\{.*?\}', bg_mesh_css, content, flags=re.DOTALL)

with open('ui/src/index.css', 'w', encoding='utf-8') as f:
    f.write(content)

print("Theme updated to vibrant palette!")
