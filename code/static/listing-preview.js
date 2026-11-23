(() => {
    const form = document.getElementById("listing-form");
    const preview = document.querySelector(".listing-preview");
    const image = document.getElementById("preview-image");
    const placeholder = document.getElementById("preview-placeholder");
    const fields = form.elements;
    const currency = new Intl.NumberFormat("zh-CN", {
        style: "currency", currency: "CNY", currencyDisplay: "narrowSymbol",
    });
    let objectUrl = null;

    function updateText() {
        document.getElementById("preview-title").textContent = fields.title.value.trim() || "商品名称";
        const price = fields.price.valueAsNumber;
        document.getElementById("preview-price").textContent =
            Number.isFinite(price) && price > 0 ? currency.format(price) : "待定价";
        const condition = fields.condition.selectedOptions[0];
        document.getElementById("preview-meta").textContent = [
            fields.category.value && fields.category.selectedOptions[0].text,
            fields.condition.value && condition.text,
            fields.meeting_place.value.trim(),
        ].filter(Boolean).join(" · ");
    }

    function showImage(visible) {
        image.hidden = !visible;
        placeholder.hidden = visible;
    }

    function updatePhoto() {
        if (objectUrl) URL.revokeObjectURL(objectUrl);
        objectUrl = null;
        const kept = form.querySelector('.manage-images label:has(input:not(:checked)) img');
        const firstFile = fields.images.files[0];
        let source = kept?.src;
        if (!source && firstFile) {
            objectUrl = URL.createObjectURL(firstFile);
            source = objectUrl;
        }
        placeholder.textContent = "添加照片后预览";
        showImage(false);
        if (source) image.src = source;
        else image.removeAttribute("src");
    }

    image.addEventListener("load", () => showImage(true));
    image.addEventListener("error", () => {
        placeholder.textContent = "无法预览这张图片";
        showImage(false);
    });
    form.addEventListener("input", updateText);
    form.addEventListener("change", event => {
        if (["remove_images", "images"].includes(event.target.name)) updatePhoto();
    });
    updateText();
    updatePhoto();
    preview.hidden = false;
})();
