            // Highlight active sidebar link
            (() => {
                const links = document.querySelectorAll('#sidebarNav .sidebar-link');
                const path = window.location.pathname.replace(/\/$/, '');
                links.forEach(link => {
                    // The path only: admin links carry ?user=
                    let href = new URL(link.getAttribute('href'), window.location.href).pathname.replace(/\/$/, '');
                    // Only mark active if exact match
                    if (href === path) {
                        link.classList.add('active');
                    } else {
                        link.classList.remove('active');
                    }
                });
            })();
            
