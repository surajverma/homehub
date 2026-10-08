// Translations for page scripts. window.I18N is set in base.html: { lang, messages }.
// I18N.locale is what to pass to toLocaleString() and friends: English keeps the browser's own format.
// Keys are the English text, so English needs no entries and a missing translation shows the English.
(function(){
    const data = window.I18N || {};
    const messages = data.messages || {};
    data.locale = (data.lang && data.lang !== 'en') ? data.lang : undefined;
    window.I18N = data;
    const ORDER = ['zero', 'one', 'two', 'few', 'many', 'other'];
    let rules = null;
    let categories = ['one', 'other'];
    try {
        rules = new Intl.PluralRules(data.lang || 'en');
        const supported = rules.resolvedOptions().pluralCategories;
        categories = ORDER.filter(function(c){ return supported.indexOf(c) !== -1; });
    } catch(e) { rules = null; }

    function fill(text, params){
        if (!params) return text;
        return text.replace(/\{(\w+)\}/g, function(match, name){
            return Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match;
        });
    }

    // t(text, params): params fill {name}-style placeholders in the text
    window.t = function(key, params){
        const found = messages[key];
        return fill(typeof found === 'string' ? found : key, params);
    };

    // tc(context, text, params) is t() for a word whose translation depends on where it is used
    window.tc = function(context, key, params){
        const found = messages[context + '\u0004' + key];
        return fill(typeof found === 'string' ? found : key, params);
    };

    data.escape = function(text){
        return String(text).replace(/[&<>"']/g, function(c){
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    };

    // th() is t() made safe to put inside an HTML string (innerHTML, template literals)
    window.th = function(key, params){ return data.escape(window.t(key, params)); };

    // tn(one, other, count, params) picks the plural form for the count; {count} is filled in for you
    window.tn = function(one, other, count, params){
        const all = Object.assign({ count: count }, params);
        const forms = messages[one];
        if (Array.isArray(forms) && forms.length){
            const index = rules ? categories.indexOf(rules.select(count)) : (count === 1 ? 0 : 1);
            return fill(forms[Math.min(index < 0 ? forms.length - 1 : index, forms.length - 1)], all);
        }
        return fill(count === 1 ? one : other, all);
    };
})();
