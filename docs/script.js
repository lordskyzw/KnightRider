document.addEventListener('DOMContentLoaded', () => {
    // 1. SVG Writing Effect for Title
    const titleText = document.querySelector('.knight-text-main');
    if (titleText) {
        // We can simulate a drawing effect by manipulating stroke-dasharray
        // But for a simple 'engineer' look, we might just use a CSS fade/reveal or typewriting
        // Let's add a class to trigger CSS animation if needed
        titleText.classList.add('writing-active');
    }

    // 2. Timeline Nodes Activation (Scroll reveal)
    const nodes = document.querySelectorAll('.node');
    const observerOptions = {
        threshold: 0.5
    };

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('active');
            }
        });
    }, observerOptions);

    nodes.forEach(node => {
        observer.observe(node);
    });

    // 3. Randomize Label Rotations for 'Sketched' feel
    const labels = document.querySelectorAll('.label-item');
    labels.forEach(label => {
        const randomRot = (Math.random() * 10 - 5).toFixed(2);
        label.style.setProperty('--r', Math.random());
        label.style.transform = `rotate(${randomRot}deg)`;
    });

    // 4. Smooth Scrolling for Doodle Links
    document.querySelectorAll('.doodle-link').forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            const targetId = link.getAttribute('href');
            const target = document.querySelector(targetId);
            if (target) {
                target.scrollIntoView({
                    behavior: 'smooth'
                });
            }
        });
    });

    // 5. Typewriter Effect for 'Active Writing' literature
    const writingElements = document.querySelectorAll('.writing-text');
    writingElements.forEach(el => {
        const text = el.innerText;
        el.innerText = '';
        let i = 0;
        const type = () => {
            if (i < text.length) {
                el.innerText += text.charAt(i);
                i++;
                setTimeout(type, 50 + Math.random() * 50);
            }
        };

        // Trigger when parent node is active
        const parentNode = el.closest('.node');
        if (parentNode) {
            const nodeObserver = new IntersectionObserver((entries) => {
                if (entries[0].isIntersecting) {
                    type();
                    nodeObserver.disconnect();
                }
            }, { threshold: 1 });
            nodeObserver.observe(parentNode);
        } else {
            type();
        }
    });

    // 6. Double-Page Roadmap Book Logic (Mobile)
    const bookNodes = Array.from(document.querySelectorAll('.node'));
    const prevBtn = document.getElementById('prevPage');
    const nextBtn = document.getElementById('nextPage');
    const currentPageEl = document.getElementById('currentPage');
    const leftPage = document.getElementById('leftPageContent');
    const rightPage = document.getElementById('rightPageContent');
    const bookContainer = document.querySelector('.roadmap-book');

    let currentSpread = 0; // 0: phases 1&2, 1: phases 3&4
    const totalSpreads = Math.ceil(bookNodes.length / 2);

    const updateBook = () => {
        if (window.innerWidth < 1024 && leftPage && rightPage) {
            const startIdx = currentSpread * 2;

            // Add flip animation classes
            if (bookContainer) {
                bookContainer.classList.add('page-flip-exit');
                setTimeout(() => {
                    // Populate Left Page
                    const leftNode = bookNodes[startIdx];
                    leftPage.innerHTML = leftNode ? leftNode.querySelector('.node-popup').outerHTML : '';

                    // Populate Right Page
                    const rightNode = bookNodes[startIdx + 1];
                    rightPage.innerHTML = rightNode ? rightNode.querySelector('.node-popup').outerHTML : '<div class="node-popup"><h4>End of Road</h4><p>Stay tuned for more updates!</p></div>';

                    bookContainer.classList.remove('page-flip-exit');
                    bookContainer.classList.add('page-flip-enter');
                    setTimeout(() => {
                        bookContainer.classList.remove('page-flip-enter');
                        // Trigger typewriter on new content
                        const writingTexts = bookContainer.querySelectorAll('.writing-text');
                        writingTexts.forEach(el => el.classList.remove('typing-started'));
                        // The intersection observer from section 5 will handle the rest if visible
                    }, 600);
                }, 600);
            }

            if (currentPageEl) currentPageEl.innerText = currentSpread + 1;

            // Toggle button states
            if (prevBtn) prevBtn.disabled = currentSpread === 0;
            if (nextBtn) nextBtn.disabled = currentSpread >= totalSpreads - 1;
        }
    };

    if (prevBtn && nextBtn) {
        prevBtn.addEventListener('click', () => {
            if (currentSpread > 0) {
                currentSpread--;
                updateBook();
            }
        });

        nextBtn.addEventListener('click', () => {
            if (currentSpread < totalSpreads - 1) {
                currentSpread++;
                updateBook();
            }
        });
    }

    // Initial population for mobile
    if (window.innerWidth < 1024) {
        updateBook();
    }

    window.addEventListener('resize', () => {
        if (window.innerWidth < 1024) {
            updateBook();
        }
    });

    // 7. Coffee Stain Interaction (Just for fun)
    const stain = document.querySelector('.coffee-stain');
    if (stain) {
        stain.addEventListener('mouseover', () => {
            stain.style.transform = `scale(1.1) rotate(${Math.random() * 20}deg)`;
        });
        stain.addEventListener('mouseout', () => {
            stain.style.transform = `scale(1) rotate(0deg)`;
        });
    }
});
