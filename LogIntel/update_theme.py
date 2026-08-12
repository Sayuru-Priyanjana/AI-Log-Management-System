import re

with open('ui/src/index.css', 'r', encoding='utf-8') as f:
    content = f.read()

light_theme = """
:root {
  --bg-color: #f8f9fa;
  --bg-color-raised: #e9ecef;

  --text-primary: #212529;
  --text-secondary: #343a40;
  --text-tertiary: #495057;

  --accent-color: #212529;
  --accent-2: #495057;
  --accent-hover: #343a40;
  --accent-soft: rgba(33, 37, 41, 0.1);
  --accent-glow: rgba(33, 37, 41, 0.3);
  --accent-gradient: linear-gradient(135deg, #212529 0%, #495057 100%);

  --glass-bg: rgba(248, 249, 250, 0.65);
  --glass-bg-soft: rgba(233, 236, 239, 0.4);
  --glass-bg-solid: #f8f9fa;
  --glass-border: #ced4da;
  --glass-highlight: rgba(255, 255, 255, 1);

  --success: #6c757d;
  --success-bg: rgba(108, 117, 125, 0.1);
  --success-border: rgba(108, 117, 125, 0.25);
  --error: #212529;
  --error-bg: rgba(33, 37, 41, 0.1);
  --error-border: rgba(33, 37, 41, 0.25);
  --warning: #495057;
  --warning-bg: rgba(73, 80, 87, 0.1);
  --warning-border: rgba(73, 80, 87, 0.25);

  --violet: #adb5bd;
  --violet-bg: rgba(173, 181, 189, 0.1);
  --violet-border: rgba(173, 181, 189, 0.25);

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
  --bg-color: #070808;
  --bg-color-raised: #141719;

  --text-primary: #fefefe;
  --text-secondary: #ced4da;
  --text-tertiary: #adb5bd;

  --accent-color: #e9ecef;
  --accent-2: #dee2e6;
  --accent-hover: #f8f9fa;
  --accent-soft: rgba(233, 236, 239, 0.15);
  --accent-glow: rgba(233, 236, 239, 0.4);
  --accent-gradient: linear-gradient(135deg, #e9ecef 0%, #dee2e6 100%);

  --glass-bg: rgba(14, 15, 17, 0.65);
  --glass-bg-soft: rgba(11, 12, 13, 0.4);
  --glass-bg-solid: #1b1f22;
  --glass-border: #343a40;
  --glass-highlight: rgba(255, 255, 255, 0.15);

  --success: #ced4da;
  --success-bg: rgba(206, 212, 218, 0.1);
  --success-border: rgba(206, 212, 218, 0.2);
  --error: #e9ecef;
  --error-bg: rgba(233, 236, 239, 0.1);
  --error-border: rgba(233, 236, 239, 0.2);
  --warning: #dee2e6;
  --warning-bg: rgba(222, 226, 230, 0.1);
  --warning-border: rgba(222, 226, 230, 0.2);

  --violet: #adb5bd;
  --violet-bg: rgba(173, 181, 189, 0.1);
  --violet-border: rgba(173, 181, 189, 0.2);

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
    radial-gradient(at 0% 0%, rgba(73, 80, 87, 0.15) 0px, transparent 50%),
    radial-gradient(at 100% 0%, rgba(108, 117, 125, 0.15) 0px, transparent 50%),
    radial-gradient(at 100% 100%, rgba(33, 37, 41, 0.1) 0px, transparent 50%),
    radial-gradient(at 0% 100%, rgba(52, 58, 64, 0.05) 0px, transparent 50%);
  filter: blur(60px);
  animation: pulseMesh 15s ease-in-out infinite alternate;
}
:root[data-theme='dark'] .bg-mesh {
  background-image: 
    radial-gradient(at 0% 0%, rgba(206, 212, 218, 0.12) 0px, transparent 50%),
    radial-gradient(at 100% 0%, rgba(222, 226, 230, 0.12) 0px, transparent 50%),
    radial-gradient(at 100% 100%, rgba(233, 236, 239, 0.08) 0px, transparent 50%),
    radial-gradient(at 0% 100%, rgba(248, 249, 250, 0.05) 0px, transparent 50%);
}
""".strip()

content = re.sub(r':root\s*\{[^}]*--ease-out:[^}]*\}', light_theme, content, flags=re.DOTALL)
content = re.sub(r':root\[data-theme=\'dark\'\]\s*\{[^}]*\}', dark_theme, content, flags=re.DOTALL)
content = re.sub(r'\.bg-mesh\s*\{.*?:root\[data-theme=\'dark\'\]\s*\.bg-mesh\s*\{.*?\}', bg_mesh_css, content, flags=re.DOTALL)

with open('ui/src/index.css', 'w', encoding='utf-8') as f:
    f.write(content)

print("Theme updated!")
